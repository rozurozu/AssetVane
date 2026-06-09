"""米株ユニバース同期ジョブ — NASDAQ Trader directory から us_stocks を同期する（Phase 7(B-1)）。

ADR-031/039（米市場分離・UsEquityAdapter）。`UsEquityAdapter.fetch_universe()` で NASDAQ Trader
directory（nasdaqlisted ＋ otherlisted）から普通株＋ETF の一覧（symbol/company_name/is_etf）を取り、
`repo.upsert_us_stocks` で冪等更新する（ADR-002）。upsert_us_stocks は渡された列だけ更新するため、
ここで財務素（eps/bps 等）は触らず、後続ウェーブの fetch_us_fundamentals が低頻度ローテで埋める
（partial update を壊さない）。例外はジョブ境界で握り JobResult(ok=False) で返す（runner が Discord
通知＝ADR-018・sync_master.py 同型）。
"""

from __future__ import annotations

import logging

from app.adapters.us_equity import UsEquityAdapter
from app.batch.runner import JobResult
from app.db import repo

logger = logging.getLogger(__name__)


def run(adapter: UsEquityAdapter | None = None) -> JobResult:
    """米株ユニバースを同期する（Phase 7(B-1)）。

    `adapter` 引数でテスト用 fake を注入できる（実 HTTP に出さない＝testing-strategy）。
    例外（UsEquityAdapterError 等）は握って JobResult(ok=False) で返す（runner が Discord 通知）。
    """
    try:
        adapter = adapter or UsEquityAdapter()
        rows = adapter.fetch_universe()
        # symbol が欠けた行は弾く（PK が NULL だと UPSERT が壊れる・sync_master 同型）。
        rows = [r for r in rows if r.get("symbol")]
        n = repo.upsert_us_stocks(rows)
        return JobResult(
            name="sync_us_universe",
            ok=True,
            rows=n,
            detail=f"NASDAQ Trader directory から {n} 銘柄 UPSERT",
        )
    except Exception as exc:  # noqa: BLE001 — ジョブ境界で握り runner に返す
        logger.exception("sync_us_universe が失敗")
        return JobResult(name="sync_us_universe", ok=False, rows=0, detail=f"失敗: {exc}")
