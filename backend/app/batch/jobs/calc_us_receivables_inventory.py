"""売掛/在庫の質ジョブ（US）— 保有米株の DSO/DIO・受取債権/在庫 YoY を焼く（ADR-064 #2・JP 対称）。

yfinance の年次 balance_sheet（Receivables/Inventory）＋income_stmt（Total Revenue/Cost Of Revenue）
を UsEquityAdapter 経由で取り、services.edinetdb_quality（源非依存）が quant.valuation に畳んで
us_valuation_snapshots の #2 列を UPDATE する（JP の calc_receivables_inventory と対称）。
calc_us_valuation
が焼いた行を前提に**既存行を UPDATE**（NIGHTLY 順で calc_us_valuation の後）。

対象は米株保有（us_holdings）に限定（提示専用ユニバース全件は重い）。各 symbol は
_REFRESH_INTERVAL_DAYS あけて再取得（
fetch_meta で per-symbol 追跡＝財務は四半期更新ゆえ週次で十分）。
個別 symbol の失敗は握って後続継続（ADR-018）。ジョブ全体の例外は JobResult(ok=False) で返す。
"""

from __future__ import annotations

import logging
from datetime import date

from app.adapters.us_equity import UsEquityAdapter, UsEquityAdapterError
from app.batch import state
from app.batch.runner import JobResult
from app.db import repo
from app.db.engine import get_engine
from app.services import edinetdb_quality

logger = logging.getLogger(__name__)

_NAME = "calc_us_receivables_inventory"
_META_PREFIX = "us_recv_inv:"
# 各 symbol の #2 を何日あけて再取得するか（財務は四半期更新ゆえ週次で十分・JP 既定と同じ思想）。
_REFRESH_INTERVAL_DAYS = 7


def _recently_fetched(conn, symbol: str, today: str) -> bool:
    """この symbol の #2 を _REFRESH_INTERVAL_DAYS 以内に取得済みか（cadence・fetch_meta 追跡）。"""
    meta = repo.get_fetch_meta(conn, f"{_META_PREFIX}{symbol}")
    last = (meta or {}).get("last_fetched_date")
    if not last:
        return False
    try:
        gap = (date.fromisoformat(today) - date.fromisoformat(last)).days
    except ValueError:
        return False
    return gap < _REFRESH_INTERVAL_DAYS


def run() -> JobResult:
    """保有米株の売掛/在庫の質を yfinance から焼く（cadence・ADR-064 #2・JP 対称）。"""
    try:
        with get_engine().connect() as conn:
            symbols = [h["symbol"] for h in repo.list_us_holdings(conn)]
        if not symbols:
            return JobResult(name=_NAME, ok=True, rows=0, detail="米株保有なしのため skip")

        adapter = UsEquityAdapter()
        today = date.today().isoformat()
        updated = 0
        processed = 0
        skipped_cadence = 0

        # 停止を最内ループでも見る（stop_aware＝ADR-036 追補／停止フラグはファイル＝ADR-070）。
        for symbol in state.stop_aware(symbols):
            with get_engine().connect() as conn:
                if _recently_fetched(conn, symbol, today):
                    skipped_cadence += 1
                    continue
            try:
                fins = adapter.fetch_balance_sheet(symbol)
                processed += 1
                quality = edinetdb_quality.compute_quality_from_financials(fins)
                if quality:
                    with get_engine().begin() as conn:
                        if repo.update_us_valuation_receivables_inventory(conn, symbol, quality):
                            updated += 1
                repo.upsert_fetch_meta(f"{_META_PREFIX}{symbol}", today)
            except UsEquityAdapterError as exc:
                logger.warning("calc_us_receivables_inventory symbol=%s 取得失敗: %s", symbol, exc)
                continue

        return JobResult(
            name=_NAME,
            ok=True,
            rows=updated,
            detail=(
                f"#2 売掛/在庫 {updated} 件更新（取得 {processed}・cadence skip {skipped_cadence}）"
            ),
        )
    except Exception as exc:  # noqa: BLE001 — ジョブ境界で握り runner に返す（ADR-018）
        logger.exception("calc_us_receivables_inventory が失敗")
        return JobResult(name=_NAME, ok=False, rows=0, detail=f"失敗: {exc}")
