"""US #2 売掛/在庫の質: yfinance balance_sheet の正規化＋夜間ジョブの担保（ADR-064・JP 対称）。

アダプタの fetch_balance_sheet が yfinance の DataFrame を内部行へ正規化すること、ジョブが
us_holdings に絞って us_valuation_snapshots の #2 列を UPDATE することを、ネット非依存で固定。
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from app.adapters.us_equity import YahooUsEquitySource
from app.batch.jobs import calc_us_receivables_inventory
from app.db import repo
from app.db.engine import get_engine


def _fake_financials() -> tuple[pd.DataFrame, pd.DataFrame]:
    """yfinance 形（index=項目名・columns=決算日・新しい順）の BS/PL を作る。"""
    cols = [pd.Timestamp("2025-09-30"), pd.Timestamp("2024-09-30")]
    bs = pd.DataFrame(
        {
            cols[0]: [150.0, 260.0, 8000.0, 2000.0, 3000.0, 500.0],
            cols[1]: [100.0, 200.0, 7000.0, 1800.0, 2800.0, 400.0],
        },
        index=[
            "Receivables",
            "Inventory",
            "Current Assets",
            "Investments And Advances",
            "Total Liabilities Net Minority Interest",
            "Cash And Cash Equivalents",
        ],
    )
    income = pd.DataFrame(
        {cols[0]: [1100.0, 770.0], cols[1]: [1000.0, 700.0]},
        index=["Total Revenue", "Cost Of Revenue"],
    )
    return bs, income


def test_fetch_balance_sheet_normalizes_rows() -> None:
    src = YahooUsEquitySource(fetch_financials=lambda sym: _fake_financials())
    rows = src.fetch_balance_sheet("AAPL")
    assert len(rows) == 2
    latest = next(r for r in rows if r["fiscal_year"] == 2025)
    assert latest["receivables"] == 150.0
    assert latest["inventory"] == 260.0
    assert latest["revenue"] == 1100.0
    assert latest["cost_of_sales"] == 770.0
    assert latest["disclosed_date"] == "2025-09-30"
    # 清原式ネットキャッシュの BS 項目も正規化される（ADR-079・US はフル式）
    assert latest["current_assets"] == 8000.0
    assert latest["investment_securities"] == 2000.0
    assert latest["total_liabilities"] == 3000.0
    assert latest["cash"] == 500.0


class _FakeUsAdapter:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def fetch_balance_sheet(self, symbol: str) -> list[dict[str, Any]]:
        return self._rows


def _seed_us_holding_with_snapshot(symbol: str) -> None:
    repo.upsert_us_stocks([{"symbol": symbol, "company_name": "Apple", "updated_at": "2026-06-30"}])
    repo.upsert_us_valuation_snapshots(
        [{"symbol": symbol, "as_of_date": "2026-06-27", "close": 200.0, "updated_at": "2026-06-30"}]
    )
    with get_engine().begin() as conn:
        repo.upsert_us_holding(
            conn, {"symbol": symbol, "shares": 10.0, "avg_cost": 150.0, "avg_cost_jpy": 22000.0}
        )


def test_us_job_updates_snapshot_for_holding(temp_db, monkeypatch) -> None:
    """保有米株→ #2 列が us_valuation_snapshots へ UPDATE される（JP 対称）。"""
    _seed_us_holding_with_snapshot("AAPL")
    rows = [
        {
            "fiscal_year": 2024,
            "disclosed_date": "2024-09-30",
            "receivables": 100.0,
            "inventory": 200.0,
            "revenue": 1000.0,
            "gross_profit": None,
            "cost_of_sales": 700.0,
        },
        {
            "fiscal_year": 2025,
            "disclosed_date": "2025-09-30",
            "receivables": 150.0,
            "inventory": 260.0,
            "revenue": 1100.0,
            "gross_profit": None,
            "cost_of_sales": 770.0,
            "current_assets": 8000.0,
            "investment_securities": 2000.0,
            "total_liabilities": 3000.0,
            "cash": 500.0,
        },
    ]
    monkeypatch.setattr(
        calc_us_receivables_inventory, "UsEquityAdapter", lambda: _FakeUsAdapter(rows)
    )

    result = calc_us_receivables_inventory.run()
    assert result.ok is True
    assert result.rows == 1

    with get_engine().connect() as conn:
        snap = repo.get_us_valuation_snapshot(conn, "AAPL")
    assert snap is not None
    assert abs(snap["receivables_growth_yoy"] - 0.5) < 1e-9
    assert snap["inventory_turnover_days"] is not None
    # 清原式ネットキャッシュ（US フル式）が焼かれる: 8000 + 2000×0.7 − 3000 = 6400（ADR-079）
    assert snap["net_cash"] == 6400.0


def test_us_job_skips_when_no_holdings(temp_db) -> None:
    """米株保有なしなら ok=True で skip（ネットに出ない）。"""
    result = calc_us_receivables_inventory.run()
    assert result.ok is True
    assert result.rows == 0
