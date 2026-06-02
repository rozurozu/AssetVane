"""repo の UPSERT 冪等性と読み取りクエリ（Phase 0 完了条件の核＝再取得で重複しない）。"""

from __future__ import annotations

from app.db import repo
from app.db.engine import get_engine

STOCK = {
    "code": "72030",
    "company_name": "トヨタ自動車",
    "sector33_code": "3700",
    "sector17_code": "6",
    "market_code": "0111",
    "is_etf": 0,
    "updated_at": "2026-06-02T00:00:00+00:00",
}


def _quote(date: str, close: float) -> dict:
    return {
        "code": "72030",
        "date": date,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 100.0,
        "adj_close": close,
    }


def test_upsert_is_idempotent(temp_db) -> None:
    """同じ行を2回入れても重複せず、値は更新される。"""
    repo.upsert_stocks([STOCK])
    repo.upsert_daily_quotes([_quote("2026-03-10", 3000.0)])
    # 2 回目（close を変える）→ 行数は増えず、値は上書き。
    repo.upsert_stocks([STOCK])
    repo.upsert_daily_quotes([_quote("2026-03-10", 3500.0)])

    with get_engine().connect() as conn:
        stocks = repo.list_stocks(conn)
        quotes = repo.get_quotes(conn, "72030")
    assert len(stocks) == 1
    assert len(quotes) == 1
    assert quotes[0]["close"] == 3500.0  # 上書きされている


def test_get_quotes_orders_and_filters(temp_db) -> None:
    repo.upsert_stocks([STOCK])
    repo.upsert_daily_quotes(
        [_quote("2026-03-12", 3.0), _quote("2026-03-10", 1.0), _quote("2026-03-11", 2.0)]
    )
    with get_engine().connect() as conn:
        all_q = repo.get_quotes(conn, "72030")
        windowed = repo.get_quotes(conn, "72030", from_="2026-03-11", to="2026-03-11")
    assert [q["date"] for q in all_q] == ["2026-03-10", "2026-03-11", "2026-03-12"]  # 昇順
    assert [q["date"] for q in windowed] == ["2026-03-11"]  # from/to が効く


def test_get_stock_missing_returns_none(temp_db) -> None:
    with get_engine().connect() as conn:
        assert repo.get_stock(conn, "99999") is None
