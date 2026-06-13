"""テーマ grounded タグ付けの市場共通コア（tag_us_themes / tag_jp_themes の実体）。

設計の真実: docs/decisions.md ADR-050 改訂（全ユニバース grounded 事前タグ）・ADR-033
（夜あたり天井のローテ cadence）・ADR-018（部分失敗の握り）・batch-pattern。

tag_us_themes（段階A・US）と tag_jp_themes（段階B・JP）は約8割同一だったため、共通ロジックを
ここに 1 本化する（tasks/review-2026-06-12.md §3・差分カーソル/スロットルを共通化した
c2fad92 と同じ重複返済）。US/JP の違いは run_theme_tagging の引数（cap・選定クエリ・タガー・
bump 最適化の有無）に押し込む。各ジョブはモジュール docstring（NIGHTLY 順序の根拠を持つ）と
run() を残し、本体をここへ委譲する（_cursor.py / _http.py と同じ `_`接頭の共通モジュール）。

タガー（tag_stock_themes）は引数で受け取る＝各ジョブが**自分の名前空間から渡す**ことで、
テストの `monkeypatch.setattr(tag_us_themes, "tag_stock_themes", fake)` が従来どおり効く
（ジョブモジュール属性の patch が委譲後も生きる・testing-strategy）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Connection

from app.batch.runner import JobResult
from app.config import settings
from app.db import repo
from app.db.engine import get_engine
from app.reference.theme_seeds import SEED_THEMES

logger = logging.getLogger(__name__)


def _source_key(market: str, code: str) -> str:
    """銘柄ごとの fetch_meta source キー（例: 'us_themes:AAPL' / 'jp_themes:72030'）。"""
    return f"{market.lower()}_themes:{code}"


def _now_iso() -> str:
    """現在時刻を ISO8601（UTC・時刻まで）で返す（fetch_meta カーソルの前提形式・ADR-050）。"""
    return datetime.now(UTC).isoformat()


def run_theme_tagging(
    *,
    market: str,
    cap: int,
    list_codes: Callable[[Connection, int], list[str]],
    tagger: Callable[..., Coroutine[Any, Any, dict[str, Any]]],
    use_bump_optimization: bool,
) -> JobResult:
    """市場共通のテーマタギング本体（ADR-050/033/018）。

    種テーマ投入（冪等）→ 選定（list_codes）→ 銘柄ループ（1 銘柄の失敗は握って継続）→ 末尾の
    時間窓 prune（market 限定）。失敗が 1 件でもあれば ok=False（runner が Discord に通知）。
    US/JP の唯一の差はこの引数（cap・選定クエリ・タガー・bump 最適化の有無）に押し込む。

    use_bump_optimization=True（JP 段階B 固有）: 説明テキストが前回タグ以降に未変化（既タグ済み
    かつ fetched_at <= last_fetched_date）なら LLM を呼ばず bump_stock_themes_last_seen で
    last_seen_at だけ bump する安価パス（小さい調査済み母集団は毎晩全件ローテ選定されるため）。
    US（段階A）は語彙ドリフト追従で常に LLM 再タグ＝False（挙動が割れるが意図・ADR-050）。
    """
    job = f"tag_{market.lower()}_themes"
    try:
        # 種テーマは毎晩冪等に仕込む（種の追加が次の夜に自動反映・どちらが無効でも安全側・冪等）。
        n_seeded = repo.insert_themes_if_absent(list(SEED_THEMES), _now_iso())
        with get_engine().connect() as conn:
            codes = list_codes(conn, cap)
    except Exception as exc:  # noqa: BLE001 — ジョブ境界で握り runner に返す
        logger.exception("%s: 種投入/巡回対象の選定に失敗", job)
        return JobResult(name=job, ok=False, rows=0, detail=f"対象選定失敗: {exc}")

    if not codes:
        return JobResult(
            name=job,
            ok=True,
            rows=0,
            detail=f"company_descriptions({market}) が空＝巡回対象なし",
        )

    n_ok = 0
    n_new_themes = 0
    n_bumped = 0
    failures: list[str] = []
    for code in codes:
        try:
            if use_bump_optimization:
                with get_engine().connect() as conn:
                    meta = repo.get_fetch_meta(conn, _source_key(market, code))
                    desc = repo.get_company_description(conn, market, code)
                last_tagged = (meta or {}).get("last_fetched_date")
                desc_fetched = (desc or {}).get("fetched_at")
                # 説明が前回タグ以降に未変化なら LLM を呼ばず last_seen_at だけ bump する安価パス
                # （段階B 固有・モジュール docstring 参照）。①未タグ・②変化は下の LLM へ。
                if last_tagged and desc_fetched and desc_fetched <= last_tagged:
                    repo.bump_stock_themes_last_seen(
                        market=market, code=code, last_seen_at=_now_iso()
                    )
                    repo.upsert_fetch_meta(_source_key(market, code), _now_iso())
                    n_ok += 1
                    n_bumped += 1
                    continue
            # 読み取り専用 conn を渡す（書き込みは repo W1 関数が自前 begin・theme_tagger 規律）。
            with get_engine().connect() as conn:
                result = asyncio.run(tagger(conn, market=market, code=code))
            # skip（説明テキスト空）も成功扱いでカーソルを前進させる（同銘柄の再選定ループ防止。
            # テキストが変化すれば fetched_at 比較で再び優先される＝ADR-050 の差分定義）。
            repo.upsert_fetch_meta(_source_key(market, code), _now_iso())
            n_ok += 1
            n_new_themes += int(result.get("n_new_themes", 0) or 0)
        except Exception as exc:  # noqa: BLE001 — 銘柄境界で握り後続銘柄を止めない（ADR-018）
            logger.exception("%s: 銘柄 %s のタグ付けに失敗", job, code)
            repo.mark_fetch_attempt_failed(_source_key(market, code))
            failures.append(f"{code}: {exc}")

    # 末尾の時間窓 prune（market 限定＝他市場のタグを誤って枯らさない安全弁・ADR-050）。
    pruned = 0
    try:
        cutoff = (datetime.now(UTC) - timedelta(days=settings.theme_prune_days)).isoformat()
        pruned = repo.prune_stale_stock_themes(market=market, cutoff_iso=cutoff)
    except Exception as exc:  # noqa: BLE001 — prune 失敗もジョブ内で握り detail に集約（ADR-018）
        logger.exception("%s: 時間窓 prune に失敗", job)
        failures.append(f"prune: {exc}")

    detail = (
        f"タグ付け {len(codes)} 件中 成功 {n_ok}・失敗 {len(failures)}（夜天井 {cap}）"
        f"・新テーマ {n_new_themes} 件"
    )
    if use_bump_optimization:
        detail += f"・bump のみ {n_bumped} 件"  # 段階B 固有（US の detail は従来どおり付けない）
    detail += f"・prune {pruned} 行"
    if n_seeded:
        detail += f"・種テーマ投入 {n_seeded} 件"
    if failures:
        detail += " / 失敗詳細: " + "; ".join(failures[:5])
    return JobResult(name=job, ok=not failures, rows=n_ok, detail=detail)
