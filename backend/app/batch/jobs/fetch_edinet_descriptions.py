"""夜間バッチ: EDINET 有報「事業の内容」を取得・要約して company_descriptions へ焼く（段階C）。

設計の真実: docs/decisions.md ADR-056（EDINET を JP の事業説明テキスト源にする）・ADR-050 改訂
（全ユニバース grounded 事前タグ・段階C＝EDINET → JP 全ユニバース）・ADR-033（夜あたり天井）・
ADR-018（部分失敗を握って後続を止めない）・ADR-020（取得 → 要約 → 本文は捨てる）。

取得モデルは **提出日クロール型**（grill 2026-06-11）。EDINET 書類一覧 API は提出日でしか引けない
ため、銘柄単位の「最新有報」解決はクロールの帰結になる。カーソルは fetch_meta('edinet:crawl') の
last_fetched_date 1 本＝「最後に**完了**した提出日」。クロール基本動作（crawl）は 1 個に統一し、
夜間差分（run）は X=カーソル翌日、バックフィル script は X=今日−窓 で起点だけ変え同じ core を呼ぶ。

1 提出日ごとに:
  1. list_documents(date) で書類一覧を取り、docTypeCode='120'（有報）かつ secCode がユニバース内
     （list_jp_universe_codes＝普通株）だけに絞る。
  2. 各銘柄で **要約 LLM を撃つ前に事前 skip**（コスト節約・ADR-050 実装メモ「dossier 優先」）:
     - 既存が source='dossier'（JP 調査済み）→ skip（dossier ⊇ EDINET ゆえ上書きしない）。
     - 既存が source='edinet' かつ disclosed_date >= 今回の period_end → skip（既に最新を持つ）。
  3. fetch_business_description(doc_id) → 事業の内容を要約（summarize_business_description）→
     upsert_company_description_edinet（保険として既存 dossier を上書きしない二重防御）。
  4. その提出日を**完了**したらカーソルを当日へ前進（部分失敗の再開点・ADR-018）。

夜あたりは edinet_nightly_max 件の要約で打ち切る（cap・差分は低 churn なので通常達しない）。cap に
達したら現提出日はカーソルを進めず break（次回その日から再開＝事前 skip で済んだ分は撃ち直さない）。
クロールで埋めた company_descriptions(JP, source='edinet') は後段の tag_jp_themes が source 不問で
拾い grounded タグ付けする（NIGHTLY 順: investigate_dossier → fetch_edinet_descriptions →
tag_jp_themes・本ジョブは tag_jp_themes の直前）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.adapters.edinet import (
    DOC_TYPE_ANNUAL_SECURITIES_REPORT,
    EdinetAdapter,
    EdinetAdapterError,
)
from app.advisor.edinet_summary import summarize_business_description
from app.batch.runner import JobResult
from app.config import settings
from app.db import repo
from app.db.engine import get_engine

logger = logging.getLogger(__name__)

# クロール進捗カーソル（fetch_meta の source キー・銘柄別 'edinet:<code>' とは別の単一カーソル）。
_CRAWL_SOURCE = "edinet:crawl"
_JST = ZoneInfo("Asia/Tokyo")


def _today_jst() -> date:
    """JST の今日（EDINET 提出日は JST 基準・夜間バッチは 02:00 JST に走る）。"""
    return datetime.now(_JST).date()


def _daterange(start: date, end: date) -> Iterator[date]:
    """start〜end（両端含む）の日付を 1 日刻みで返す（提出日クロールの巡回基底）。"""
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def _default_summarize(text: str) -> str:
    """既定の要約関数（async の summarize_business_description を asyncio.run で同期呼び・段階C）。

    夜間ジョブ/バックフィル script は同期 def なのでここで毎回イベントループを起こす
    （tag_jp_themes が asyncio.run(tag_stock_themes) する流儀と同型）。テストは差し替え可能。
    """
    return asyncio.run(summarize_business_description(text))


def crawl(
    *,
    start_date: date,
    end_date: date,
    cap: int | None,
    adapter: EdinetAdapter | None = None,
    summarize_fn: Callable[[str], str] | None = None,
    log: Callable[[str], None] | None = None,
) -> dict[str, object]:
    """提出日 start_date〜end_date をクロールして事業の内容を取り込む（差分/バックフィル共通）。

    Args:
        start_date/end_date: クロールする提出日の範囲（両端含む）。
        cap: この実行で要約まで進める件数の上限（None=無制限＝バックフィル）。
        adapter: EdinetAdapter（DI・テスト用）。None で既定生成。
        summarize_fn: 事業の内容テキスト→要約（DI・テストで LLM を避ける）。None で既定（実 LLM）。
        log: 進捗メッセージの出力先（script の print 等・None なら logger.info）。

    Returns:
        集計 dict（n_summarized/n_skip_dossier/n_skip_existing/n_no_business/failures/
        cap_reached/last_cursor/dates_done）。呼び出し側（run/script）が JobResult や print に整形。
    """
    adapter = adapter or EdinetAdapter()
    summarize_fn = summarize_fn or _default_summarize
    emit = log or (lambda m: logger.info("%s", m))

    with get_engine().connect() as conn:
        universe = repo.list_jp_universe_codes(conn)

    n_summarized = 0
    n_skip_dossier = 0
    n_skip_existing = 0
    n_no_business = 0
    dates_done = 0
    failures: list[str] = []
    cap_reached = False
    last_cursor: str | None = None

    for d in _daterange(start_date, end_date):
        iso = d.isoformat()
        try:
            docs = adapter.list_documents(iso)
        except EdinetAdapterError as exc:
            # 提出日一覧の取得そのものが失敗＝この日を飛ばすと取りこぼすため、カーソルを進めず停止。
            repo.mark_fetch_attempt_failed(_CRAWL_SOURCE)
            failures.append(f"{iso} 一覧取得: {exc}")
            emit(f"✖ {iso}: 書類一覧の取得に失敗（カーソル据え置きで停止）: {exc}")
            break

        targets = [
            doc
            for doc in docs
            if doc.get("doc_type_code") == DOC_TYPE_ANNUAL_SECURITIES_REPORT
            and doc.get("sec_code") in universe
        ]

        for doc in targets:
            if cap is not None and n_summarized >= cap:
                cap_reached = True
                break
            code = doc["sec_code"]
            period_end = doc.get("period_end")
            try:
                # 事前 skip（要約 LLM を撃つ前に・コスト節約＝ADR-050 実装メモ）。
                with get_engine().connect() as conn:
                    existing = repo.get_company_description(conn, "JP", code)
                if existing and existing.get("source") == "dossier":
                    n_skip_dossier += 1
                    continue
                if (
                    existing
                    and existing.get("source") == "edinet"
                    and period_end
                    and (existing.get("disclosed_date") or "") >= period_end
                ):
                    n_skip_existing += 1
                    continue

                fetched = adapter.fetch_business_description(doc["doc_id"])
                if not fetched or not fetched.get("text"):
                    n_no_business += 1
                    continue

                summary = summarize_fn(fetched["text"])
                if not (isinstance(summary, str) and summary.strip()):
                    n_no_business += 1
                    continue

                repo.upsert_company_description_edinet(
                    {
                        "market": "JP",
                        "code": code,
                        "source": "edinet",
                        "description_text": summary,
                        "disclosed_date": period_end,  # 有報の対象期末（テキストの基準日・ADR-056）
                        "doc_id": doc.get("doc_id"),
                        "fetched_at": datetime.now(UTC).isoformat(),
                    }
                )
                n_summarized += 1
                emit(f"  ✔ {iso} {code}: 事業の内容を要約・保存（period_end={period_end}）")
            except Exception as exc:  # noqa: BLE001 — 書類境界で握り後続を止めない（ADR-018）
                logger.exception("fetch_edinet_descriptions: %s の取込に失敗", code)
                failures.append(f"{code}({iso}): {exc}")

        if cap_reached:
            # 現提出日は未完なのでカーソルを進めない（次回この日から再開・事前 skip で重複撃たず）。
            emit(f"夜天井 {cap} 件に到達。{iso} の途中で打ち切り（カーソルは {last_cursor}）。")
            break

        # この提出日を完了＝カーソルを前進（部分失敗 doc があっても日付は進める＝liveness 優先。
        # 失敗 doc は edinet 行が無いままなので、バックフィル再走で拾い直せる）。
        repo.upsert_fetch_meta(_CRAWL_SOURCE, iso)
        last_cursor = iso
        dates_done += 1

    return {
        "n_summarized": n_summarized,
        "n_skip_dossier": n_skip_dossier,
        "n_skip_existing": n_skip_existing,
        "n_no_business": n_no_business,
        "dates_done": dates_done,
        "failures": failures,
        "cap_reached": cap_reached,
        "last_cursor": last_cursor,
    }


def _resolve_start(*, no_cursor_fallback: date) -> date:
    """クロール開始日を決める（カーソルがあれば翌日・無ければ fallback）。

    差分（run）は fallback=今日＝バックフィル未実施でも巨大遡及をせず「今から追跡」を始める。
    バックフィル script は fallback=今日−窓 を渡す（初回は窓頭から・以降はカーソルから再開）。
    """
    with get_engine().connect() as conn:
        meta = repo.get_fetch_meta(conn, _CRAWL_SOURCE)
    last = (meta or {}).get("last_fetched_date")
    if last:
        return date.fromisoformat(last) + timedelta(days=1)
    return no_cursor_fallback


def run() -> JobResult:
    """夜間差分: カーソル翌日〜今日(JST) をクロールし夜天井まで要約する（ADR-056/033/018）。

    EDINET_API_KEY 未設定なら静かに skip（ok=True・ADR-006/018 の「未設定は機能オフ」流儀）。
    カーソルが無ければ今日から追跡を始める（履歴は app.scripts.backfill_edinet が埋める）。
    書類境界の失敗は握って継続し、1 件でも失敗があれば ok=False（runner が Discord 集約通知）。
    """
    if not settings.edinet_api_key:
        return JobResult(
            name="fetch_edinet_descriptions",
            ok=True,
            rows=0,
            detail="EDINET_API_KEY 未設定のため skip（段階C 機能オフ）",
        )

    today = _today_jst()
    start = _resolve_start(no_cursor_fallback=today)
    if start > today:
        return JobResult(
            name="fetch_edinet_descriptions",
            ok=True,
            rows=0,
            detail=f"未クロールの提出日なし（カーソル {start - timedelta(days=1)}）",
        )

    cap = settings.edinet_nightly_max
    try:
        result = crawl(start_date=start, end_date=today, cap=cap)
    except Exception as exc:  # noqa: BLE001 — ジョブ境界で握り runner に返す
        logger.exception("fetch_edinet_descriptions: クロールに失敗")
        return JobResult(
            name="fetch_edinet_descriptions", ok=False, rows=0, detail=f"クロール失敗: {exc}"
        )

    failures = result["failures"]
    hit = "・到達" if result["cap_reached"] else ""
    detail = (
        f"提出日 {start}〜{today} を {result['dates_done']} 日クロール"
        f"・要約 {result['n_summarized']} 件（天井 {cap}{hit}）"
        f"・skip dossier {result['n_skip_dossier']}/既存 {result['n_skip_existing']}"
        f"・事業の内容なし {result['n_no_business']}・失敗 {len(failures)}"
    )
    if failures:
        detail += " / 失敗詳細: " + "; ".join(failures[:5])
    return JobResult(
        name="fetch_edinet_descriptions",
        ok=not failures,
        rows=int(result["n_summarized"]),
        detail=detail,
    )
