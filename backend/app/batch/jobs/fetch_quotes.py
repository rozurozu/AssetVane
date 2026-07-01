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
from app.batch.runner import JobResult
from app.config import settings
from app.db import repo
from app.db.engine import get_engine
from app.services.jquants_config import build_jquants_adapter

logger = logging.getLogger(__name__)

_SOURCE = "daily_quotes"


def _start_date(*, full_backfill: bool, today: str) -> str:
    """取得開始日を決める（spec §3.3）。

    full_backfill: today - BACKFILL_YEARS 年。
    差分: fetch_meta['daily_quotes'].last_fetched_date の**翌**営業日（曜日関係なく翌日でよい・
    土日は candidate_days が除外する）。fetch_meta 不在/NULL なら get_max_quote_date で自己修復し、
    それも無ければ full 相当（today - BACKFILL_YEARS）。
    """
    last = date.fromisoformat(today)
    if full_backfill:
        return last.replace(year=last.year - settings.backfill_years).isoformat()

    with get_engine().connect() as conn:
        meta = repo.get_fetch_meta(conn, _SOURCE)
        last_fetched = meta.get("last_fetched_date") if meta else None
        if last_fetched is None:
            last_fetched = repo.get_max_quote_date(conn)

    if last_fetched is None:
        # 初回相当: full と同じく BACKFILL_YEARS 分を頭から。
        return last.replace(year=last.year - settings.backfill_years).isoformat()

    # 取得済み最終日の翌日から（candidate_days が土日を除外する）。
    return (date.fromisoformat(last_fetched) + timedelta(days=1)).isoformat()


def run(*, full_backfill: bool = False) -> JobResult:
    """営業日ループで日足を取得し daily_quotes / fetch_meta を前進させる（spec §3.3）。

    各営業日 d で `fetch_daily_quotes_by_date(d)` → 空でなければ UPSERT。空配列の日（非営業日）
    もスキップしつつ fetch_meta を前進させる（再開境界を進める）。例外（JQuantsError 等）は握って
    JobResult(ok=False) で返す（runner が Discord 通知する）。
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
            # 空配列（祝日・臨時休場）でも fetch_meta を前進させ再開境界を進める。
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
