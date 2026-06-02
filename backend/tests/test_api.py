"""REST API（TestClient）。lifespan で alembic upgrade が走り、空 DB から動く。"""

from __future__ import annotations

from app.db import repo

STOCK = {
    "code": "72030",
    "company_name": "トヨタ自動車",
    "sector33_code": "3700",
    "sector17_code": "6",
    "market_code": "0111",
    "is_etf": 0,
    "updated_at": "2026-06-02T00:00:00+00:00",
}


def test_health(client) -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"


def test_stocks_empty_then_populated(client) -> None:
    assert client.get("/stocks").json() == []  # 最初は空
    repo.upsert_stocks([STOCK])
    repo.upsert_daily_quotes(
        [
            {
                "code": "72030",
                "date": "2026-03-10",
                "open": 1.0,
                "high": 2.0,
                "low": 0.5,
                "close": 1.5,
                "volume": 100.0,
                "adj_close": 1.5,
            }
        ]
    )
    stocks = client.get("/stocks").json()
    assert len(stocks) == 1 and stocks[0]["company_name"] == "トヨタ自動車"

    quotes = client.get("/quotes/72030").json()
    assert len(quotes) == 1 and quotes[0]["close"] == 1.5


def test_stock_detail_404(client) -> None:
    assert client.get("/stocks/99999").status_code == 404
