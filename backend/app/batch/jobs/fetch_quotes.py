"""日足取得ジョブ — 営業日ループで初回バックフィルと差分取得を同一経路で回す（spec §3.3/§3.4）。

`fetch_daily_quotes_by_date(d)` の「日付一括（全銘柄×1日）」を `calendar.candidate_days` で
営業日ループする（銘柄ループではない＝roadmap.md Phase 1 留意）。1 営業日ごとに `fetch_meta`
を前進させ、途中で落ちても翌回は続きから回せる（ADR-018 部分失敗からの再開）。
書き込みは UPSERT で冪等（ADR-002）。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from app.adapters.jquants import JQuantsCoverageError
from app.batch import calendar, state
from app.batch.jobs._cursor import backfill_start_date, resolve_differential_start
from app.batch.runner import JobResult
from app.config import settings
from app.db import repo
from app.db.engine import get_engine
from app.services.jquants_config import build_jquants_adapter

logger = logging.getLogger(__name__)

_SOURCE = "daily_quotes"


def _start_date(*, full_backfill: bool, today: str) -> str:
    """取得開始日を決める（spec §3.3・ADR-018/093）。

    full_backfill: today - BACKFILL_YEARS 年。
    差分: fetch_meta['daily_quotes'].last_fetched_date に鮮度プローブ（overlap）を重ねた地点
    （`_cursor.resolve_differential_start`＝index/us_quotes/fx_rates/fund_navs と同じ規律に統一・
    ADR-093）。取得済み日の再取得は UPSERT で冪等（ADR-002）。fetch_meta 不在/NULL なら
    get_max_quote_date で自己修復し、それも無ければ full 相当（today - BACKFILL_YEARS）。

    さらに **実データ（daily_quotes の max(date)）の翌日を start の上限**にする（ADR-093）。
    カーソルが実データより先へ飛んだ状態（＝未掲載日でロックインした痕）は overlap の幅では
    追いつけず穴が永久に残るため、実データの続きから取り直せるよう引き戻す。正常時は
    max(date) == last_fetched なので overlap 側が必ず小さく、この上限は発動しない。
    """
    backfill_start = backfill_start_date(today, settings.backfill_years)
    if full_backfill:
        return backfill_start

    with get_engine().connect() as conn:
        meta = repo.get_fetch_meta(conn, _SOURCE)
        last_fetched = meta.get("last_fetched_date") if meta else None
        max_quote = repo.get_max_quote_date(conn)

    if last_fetched is None:
        last_fetched = max_quote

    # 初回相当（last_fetched が None）なら backfill_start をそのまま返す（_cursor が吸収）。
    start = resolve_differential_start(last_fetched, backfill_start=backfill_start)

    if max_quote is not None:
        # 実データの翌日より先へは進めない（カーソル暴走からの自己修復・ADR-093）。
        resume = (date.fromisoformat(max_quote) + timedelta(days=1)).isoformat()
        start = min(start, resume)
    return start


def run(*, full_backfill: bool = False) -> JobResult:
    """営業日ループで日足を取得し daily_quotes / fetch_meta を前進させる（spec §3.3）。

    各営業日 d で `fetch_daily_quotes_by_date(d)` → 空でなければ UPSERT。空配列の日は「確定した
    **過去日**なら非営業日（祝日・臨時休場）」とみなして fetch_meta を前進させ再開境界を進めるが、
    **当日の空はまだ未掲載**なので前進させない（ADR-093＝ロックイン防止）。例外（JQuantsError 等）
    は握って JobResult(ok=False) で返す（runner が Discord 通知する）。
    """
    today = date.today().isoformat()
    start = _start_date(full_backfill=full_backfill, today=today)

    total_rows = 0
    days = 0
    frontier: str | None = None  # 契約範囲の前線に達して打ち切った日付（あれば）
    try:
        adapter = build_jquants_adapter()
        # full_backfill は数年分の営業日を 1 ジョブで回す（≒数時間）。営業日境界で should_stop を
        # 見て中断する（stop_aware・ADR-036 追補/070）。fetch_meta は処理済み日まで前進済み＝
        # 続きから再開できる（冪等・ADR-018）。
        for d in state.stop_aware(calendar.candidate_days(start, today)):
            try:
                rows = adapter.fetch_daily_quotes_by_date(d)
            except JQuantsCoverageError:
                # 範囲外日付（Free の12週遅延・格納期間外）は 400 で返る＝前線到達。ここで正常終了。
                # fetch_meta は d に進めない（直前の取得済み日のまま）。遅延窓は日々前進するので、
                # 翌晩の差分は d から再試行し、提供開始されていれば取れる（2026-06-04 本番投入）。
                frontier = d
                logger.info("fetch_quotes: 契約範囲の前線に到達（%s）。取得を打ち切る。", d)
                break
            # code/date が欠けた行は弾く（PK が NULL だと UPSERT が壊れる）。
            rows = [r for r in rows if r.get("code") and r.get("date")]
            if rows:
                total_rows += repo.upsert_daily_quotes(rows)
            elif d >= today:
                # ADR-093: **当日の空レスは「非営業日」ではなく「まだ未掲載」**。J-Quants はデータの
                # 無い日（当日・未来日）を 400 でなく 200 + {"data": []} で返すため、空レスだけでは
                # 祝日と区別が付かない。夜間バッチは 02:00 に走る＝当日の日足は必ず未掲載なので、
                # ここで前進させると翌晩の start が today に張り付き、前日以前を永久に取り逃す
                # （2026-07-02〜07-13 の日足欠損＝ロックイン）。当日は進めず翌晩に取り直す。
                logger.info("fetch_quotes: %s は未掲載（当日）。カーソルを進めない（ADR-093）。", d)
                continue
            # 空配列でも**過去日**なら祝日・臨時休場と確定できるので fetch_meta を前進させ、
            # 再開境界を進める（カレンダー API に依存しない＝calendar.py の設計）。
            repo.upsert_fetch_meta(_SOURCE, d)
            days += 1
    except Exception as exc:  # noqa: BLE001 — ジョブ境界（JQuantsError 含む）で握り runner に返す
        logger.exception("fetch_quotes が失敗（start=%s）", start)
        return JobResult(
            name="fetch_quotes",
            ok=False,
            rows=total_rows,
            detail=f"start={start} で {days} 日処理後に失敗: {exc}",
        )

    # stop_aware がループを打ち切ったか（frontier 打ち切り・自然終了と区別する・ADR-070）。
    stopped = state.should_stop()
    if stopped:
        logger.info("fetch_quotes: 停止要求を検知。取得済み日で中断（ADR-036/070）。")

    tail = f"・前線 {frontier} で打ち切り" if frontier else f"〜{today}"
    if stopped:
        tail += "・停止により中断"
    return JobResult(
        name="fetch_quotes",
        ok=True,
        rows=total_rows,
        detail=f"start={start}{tail}・営業日 {days} 日・{total_rows} 行 UPSERT",
    )
