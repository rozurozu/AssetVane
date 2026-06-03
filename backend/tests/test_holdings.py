"""holdings 再計算（services/holdings.py）の単体テスト。

既知の transactions（buy/sell 混在）→ 期待 shares・avg_cost（移動平均）を検証する。
全売却で holdings 行が消えることを確認する（phase2-spec.md §8・ADR-019）。
"""

from __future__ import annotations

import pytest

from app.db import repo
from app.services.holdings import recalc_holdings

# テスト用の銘柄・ポートフォリオ
STOCK_A = {
    "code": "72030",
    "company_name": "トヨタ自動車",
    "sector33_code": "3700",
    "sector17_code": "6",
    "market_code": "0111",
    "is_etf": 0,
    "updated_at": "2026-06-01T00:00:00+00:00",
}
STOCK_B = {
    "code": "67580",
    "company_name": "ソニーグループ",
    "sector33_code": "3600",
    "sector17_code": "7",
    "market_code": "0111",
    "is_etf": 0,
    "updated_at": "2026-06-01T00:00:00+00:00",
}


def _seed_portfolio(client) -> int:
    """先頭ポートフォリオの portfolio_id を返す（seed 済みの id=1 / Default）。"""
    resp = client.get("/portfolios")
    assert resp.status_code == 200
    portfolios = resp.json()
    assert len(portfolios) >= 1
    return portfolios[0]["portfolio_id"]


def test_buy_only_avg_cost(client) -> None:
    """buy のみのケース: shares と avg_cost が正しく計算される。"""
    repo.upsert_stocks([STOCK_A])
    pid = _seed_portfolio(client)

    # 100 株 @ 1000 を buy → avg_cost = 1000
    resp = client.post(
        "/transactions",
        json={
            "portfolio_id": pid,
            "code": "72030",
            "side": "buy",
            "shares": 100,
            "price": 1000,
            "traded_at": "2026-01-10",
        },
    )
    assert resp.status_code == 201
    holdings = resp.json()["holdings"]["holdings"]
    h = next(h for h in holdings if h["code"] == "72030")
    assert h["shares"] == pytest.approx(100.0)
    assert h["avg_cost"] == pytest.approx(1000.0)


def test_two_buys_moving_average(client) -> None:
    """2 回 buy: 移動平均取得単価が正しく更新される。

    100株@1000 → avg=1000、その後 200株@1500 → avg = (100*1000+200*1500)/300 = 1333.33...
    """
    repo.upsert_stocks([STOCK_A])
    pid = _seed_portfolio(client)

    client.post(
        "/transactions",
        json={
            "portfolio_id": pid,
            "code": "72030",
            "side": "buy",
            "shares": 100,
            "price": 1000,
            "traded_at": "2026-01-10",
        },
    )
    resp = client.post(
        "/transactions",
        json={
            "portfolio_id": pid,
            "code": "72030",
            "side": "buy",
            "shares": 200,
            "price": 1500,
            "traded_at": "2026-01-11",
        },
    )
    assert resp.status_code == 201
    holdings = resp.json()["holdings"]["holdings"]
    h = next(h for h in holdings if h["code"] == "72030")
    assert h["shares"] == pytest.approx(300.0)
    expected_avg = (100 * 1000 + 200 * 1500) / 300
    assert h["avg_cost"] == pytest.approx(expected_avg, rel=1e-5)


def test_sell_reduces_shares_keeps_avg(client) -> None:
    """sell は shares を減らし avg_cost は変えない。"""
    repo.upsert_stocks([STOCK_A])
    pid = _seed_portfolio(client)

    # buy 100株@1000
    client.post(
        "/transactions",
        json={
            "portfolio_id": pid,
            "code": "72030",
            "side": "buy",
            "shares": 100,
            "price": 1000,
            "traded_at": "2026-01-10",
        },
    )
    # sell 30株（avg_cost は変わらず shares だけ減る）
    resp = client.post(
        "/transactions",
        json={
            "portfolio_id": pid,
            "code": "72030",
            "side": "sell",
            "shares": 30,
            "price": 1200,
            "traded_at": "2026-01-12",
        },
    )
    assert resp.status_code == 201
    holdings = resp.json()["holdings"]["holdings"]
    h = next(h for h in holdings if h["code"] == "72030")
    assert h["shares"] == pytest.approx(70.0)
    assert h["avg_cost"] == pytest.approx(1000.0)  # avg は buy 時の値のまま


def test_full_sell_removes_holding(client) -> None:
    """全売却で holdings 行が消える（shares=0 は保存しない＝ADR-019）。"""
    repo.upsert_stocks([STOCK_A])
    pid = _seed_portfolio(client)

    # buy 100株
    client.post(
        "/transactions",
        json={
            "portfolio_id": pid,
            "code": "72030",
            "side": "buy",
            "shares": 100,
            "price": 1000,
            "traded_at": "2026-01-10",
        },
    )
    # 全部 sell
    resp = client.post(
        "/transactions",
        json={
            "portfolio_id": pid,
            "code": "72030",
            "side": "sell",
            "shares": 100,
            "price": 1100,
            "traded_at": "2026-01-15",
        },
    )
    assert resp.status_code == 201
    holdings = resp.json()["holdings"]["holdings"]
    codes = [h["code"] for h in holdings]
    assert "72030" not in codes, "全売却後に holdings 行が残ってはいけない"


def test_multiple_stocks(client) -> None:
    """複数銘柄を別々に管理できる。"""
    repo.upsert_stocks([STOCK_A, STOCK_B])
    pid = _seed_portfolio(client)

    client.post(
        "/transactions",
        json={
            "portfolio_id": pid,
            "code": "72030",
            "side": "buy",
            "shares": 100,
            "price": 1000,
            "traded_at": "2026-01-10",
        },
    )
    resp = client.post(
        "/transactions",
        json={
            "portfolio_id": pid,
            "code": "67580",
            "side": "buy",
            "shares": 50,
            "price": 2000,
            "traded_at": "2026-01-10",
        },
    )
    assert resp.status_code == 201
    holdings = resp.json()["holdings"]["holdings"]
    assert len(holdings) == 2
    codes = {h["code"] for h in holdings}
    assert codes == {"72030", "67580"}


def test_recalc_holdings_directly(temp_db) -> None:
    """recalc_holdings を直接呼んで repo から確認する（API 経由でない経路のテスト）。

    buy/sell を time_db に直接入れて recalc → list_holdings で確認。
    """
    from app.db.engine import get_engine

    # 銘柄・portfolio を準備
    repo.upsert_stocks([STOCK_A])

    # portfolio を直接 INSERT（temp_db は alembic 経由でないため seed がない可能性）
    from app.db.schema import portfolios

    with get_engine().begin() as conn:
        conn.execute(portfolios.insert().values(portfolio_id=1, name="Default"))

    # buy 100株@500、続けて buy 100株@1000 → avg=(100*500+100*1000)/200=750
    repo.insert_transaction(
        {
            "portfolio_id": 1,
            "code": "72030",
            "side": "buy",
            "shares": 100,
            "price": 500,
            "traded_at": "2026-01-05",
        }
    )
    repo.insert_transaction(
        {
            "portfolio_id": 1,
            "code": "72030",
            "side": "buy",
            "shares": 100,
            "price": 1000,
            "traded_at": "2026-01-06",
        }
    )
    # sell 50株（avg はそのまま）
    repo.insert_transaction(
        {
            "portfolio_id": 1,
            "code": "72030",
            "side": "sell",
            "shares": 50,
            "price": 900,
            "traded_at": "2026-01-07",
        }
    )

    recalc_holdings(1)

    with get_engine().connect() as conn:
        holdings = repo.list_holdings(conn, 1)

    assert len(holdings) == 1
    h = holdings[0]
    assert h["code"] == "72030"
    assert h["shares"] == pytest.approx(150.0)
    # avg = (100*500 + 100*1000) / 200 = 750
    assert h["avg_cost"] == pytest.approx(750.0, rel=1e-5)
