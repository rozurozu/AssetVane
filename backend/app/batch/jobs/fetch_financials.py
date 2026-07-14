"""財務取得ジョブ — 営業日ループで全銘柄の財務を取得し financials に UPSERT する（ADR-031）。

スクリーニング（/stocks）は全市場が対象のため、保有銘柄限定ではなく **全銘柄**を取る。
`fetch_financials(date=d)` の「日付一括（その日開示の全銘柄）」を `calendar.candidate_days`
で営業日ループする（fetch_quotes と同型）。1 営業日ごとに `fetch_meta['financials']` を前進させ、
途中で落ちても翌回は続きから回せる（ADR-018 再開）。書き込みは UPSERT で冪等（ADR-002）。
初回バックフィルは `full_backfill=True`（today - BACKFILL_YEARS から）。

エンドポイントは /v2/fins/summary・短縮フィールド（実機確認 2026-06・jquants.md §6 解消済み）。
"""

from __future__ import annotations

import logging
from datetime import date

from app.adapters.jquants import JQuantsCoverageError
from app.batch import calendar, state
from app.batch.jobs._cursor import backfill_start_date, resolve_differential_start
from app.batch.runner import JobResult
from app.config import settings
from app.db import repo
from app.db.engine import get_engine
from app.services.jquants_config import build_jquants_adapter

logger = logging.getLogger(__name__)

_SOURCE = "financials"


def _start_date(*, full_backfill: bool, today: str) -> str:
    """取得開始日を決める（fetch_quotes._start_date と同型・ADR-031/093）。

    full_backfill: today - BACKFILL_YEARS 年。
    差分: fetch_meta['financials'].last_fetched_date に鮮度プローブ（overlap）を重ねた地点
    （`_cursor.resolve_differential_start`・ADR-093）。不在なら max(disclosed_date) で自己修復し、
    それも無ければ full 相当（today - BACKFILL_YEARS）。

    fetch_quotes と違い「実データの翌日」を start の上限には**しない**。日足は毎営業日必ず行が
    入る（max(date) ＝処理済み最終営業日の真実）が、財務は**開示ゼロの営業日**が普通にあり、
    max(disclosed_date) は「処理済み最終日」を意味しないため上限に使えない（ADR-093）。
    """
    backfill_start = backfill_start_date(today, settings.backfill_years)
    if full_backfill:
        return backfill_start

    with get_engine().connect() as conn:
        meta = repo.get_fetch_meta(conn, _SOURCE)
        last_fetched = meta.get("last_fetched_date") if meta else None
        if last_fetched is None:
            last_fetched = repo.get_max_financial_disclosed_date(conn)

    return resolve_differential_start(last_fetched, backfill_start=backfill_start)


def run(*, full_backfill: bool = False) -> JobResult:
    """営業日ループで全銘柄の財務を取得し financials / fetch_meta を前進させる（ADR-031）。

    各営業日 d で `fetch_financials(date=d)` → 開示があれば UPSERT。開示の無い**過去日**は
    fetch_meta を前進させ再開境界を進めるが、**当日の空はまだ未掲載**なので前進させない
    （ADR-093＝fetch_quotes と同型のロックイン防止）。契約範囲外（coverage）は前線到達として
    正常終了。例外は握って JobResult(ok=False) で返す（runner が Discord 通知する・ADR-018）。
    """
    today = date.today().isoformat()
    start = _start_date(full_backfill=full_backfill, today=today)

    total_rows = 0
    days = 0
    frontier: str | None = None
    try:
        # financials は stocks.code に FK を持つ。未マスタ銘柄（新規上場が先に開示）の行で
        # バルク UPSERT が FK 違反で落ちるのを防ぐため、既知の stock コードに絞る（ADR-031）。
        # screen は stocks に JOIN するため未マスタ銘柄は元々表示外。翌晩 sync_master 後に拾える。
        with get_engine().connect() as conn:
            known_codes = set(repo.list_stock_codes(conn))

        adapter = build_jquants_adapter()
        # backfill は数年分の営業日を 1 ジョブで回す（≒数時間）。営業日境界で should_stop を見て
        # 中断する（stop_aware・ADR-036 追補/070）。fetch_meta は処理済み日まで前進済み＝続きから
        # 再開できる（冪等・ADR-018）。
        for d in state.stop_aware(calendar.candidate_days(start, today)):
            try:
                rows = adapter.fetch_financials(date=d)
            except JQuantsCoverageError:
                frontier = d
                logger.info("fetch_financials: 契約範囲の前線に到達（%s）。取得を打ち切る。", d)
                break
            # PK 構成（code/disclosed_date/fiscal_period）が欠けた行・未マスタ銘柄を弾く
            rows = [
                r
                for r in rows
                if r.get("code") in known_codes
                and r.get("disclosed_date")
                and r.get("fiscal_period")
            ]
            if rows:
                total_rows += repo.upsert_financials(rows)
            elif d >= today:
                # ADR-093: 当日の空レスは「開示なし」ではなく「まだ未掲載」（J-Quants はデータの
                # 無い日を 400 でなく 200 + {"data": []} で返す）。02:00 のバッチ時点で当日の開示は
                # 未掲載なので、ここで前進させると翌晩 start が today に張り付き、前日以前の開示を
                # 永久に取り逃す（fetch_quotes と同じロックイン）。当日は進めず翌晩に取り直す。
                logger.info(
                    "fetch_financials: %s は未掲載（当日）。カーソルを進めない（ADR-093）。", d
                )
                continue
            # 空配列でも**過去日**なら「その日は開示なし」と確定できるので fetch_meta を前進させる。
            repo.upsert_fetch_meta(_SOURCE, d)
            days += 1
    except Exception as exc:  # noqa: BLE001 — ジョブ境界（JQuantsError 含む）で握り runner に返す
        logger.exception("fetch_financials が失敗（start=%s）", start)
        return JobResult(
            name="fetch_financials",
            ok=False,
            rows=total_rows,
            detail=f"start={start} で {days} 日処理後に失敗: {exc}",
        )

    stopped = state.should_stop()  # stop_aware が打ち切ったか（ADR-070）
    tail = f"・前線 {frontier} で打ち切り" if frontier else f"〜{today}"
    if stopped:
        tail += "・停止により中断"
    return JobResult(
        name="fetch_financials",
        ok=True,
        rows=total_rows,
        detail=f"start={start}{tail}・営業日 {days} 日・{total_rows} 行 UPSERT",
    )
