"""calc_valuation ジョブのテスト（ADR-031・一時 SQLite・ネット非依存）。"""

from __future__ import annotations

from sqlalchemy import func, select

from app.batch.jobs import calc_valuation
from app.db import repo
from app.db.engine import get_engine
from app.db.schema import valuation_snapshots


def _stock(code: str) -> dict:
    return {
        "code": code,
        "company_name": f"会社{code}",
        "sector33_code": "3700",
        "sector17_code": "6",
        "market_code": "0111",
        "is_etf": 0,
        "updated_at": "2026-06-04T00:00:00+00:00",
    }


def _quote(code: str, d: str, close: float) -> dict:
    return {
        "code": code,
        "date": d,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1.0,
        "adj_close": close,
    }


def _fin(code: str) -> dict:
    return {
        "code": code,
        "disclosed_date": "2025-05-08",
        "fiscal_period": "FY",
        "net_sales": None,
        "operating_profit": None,
        "profit": None,
        "eps": 100.0,
        "bps": 1000.0,
        "dividend_per_share": 30.0,
        "shares_outstanding": 1_000_000.0,
        "treasury_shares": 0.0,
    }


def test_calc_valuation_upserts_snapshots(temp_db) -> None:
    repo.upsert_stocks([_stock("72030")])
    repo.upsert_daily_quotes([_quote("72030", "2026-06-03", 2000.0)])
    repo.upsert_financials([_fin("72030")])

    result = calc_valuation.run()
    assert result.ok is True
    assert result.rows == 1

    with get_engine().connect() as conn:
        n = conn.execute(select(func.count()).select_from(valuation_snapshots)).scalar()
        rows = repo.screen_stocks(conn, {})
    assert n == 1
    assert rows[0]["per"] == 20.0  # 2000 / 100
    assert rows[0]["pbr"] == 2.0  # 2000 / 1000

    # 2 回目で冪等（行が増えない）
    calc_valuation.run()
    with get_engine().connect() as conn:
        n2 = conn.execute(select(func.count()).select_from(valuation_snapshots)).scalar()
    assert n2 == 1
