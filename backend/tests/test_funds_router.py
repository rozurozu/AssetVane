"""投信 REST ルータ（routers/funds.py）の結合テスト（ADR-054・ADR-019）。

client フィクスチャ（一時 SQLite ＋ alembic lifespan）で /funds・/fund-transactions・
/fund-holdings・/asset-overview を検証する。ネットには出ない（NAV は repo.upsert_fund_navs で
直接焼く）。POST/PUT/DELETE /fund-transactions は素の FundHolding[] を返す契約（testing-strategy）。
"""

from __future__ import annotations

import pytest

from app.db import repo

ISIN_A = "JP90C000ABC1"
ISIN_B = "JP90C000DEF2"


def _seed_portfolio(client) -> int:
    """先頭ポートフォリオの portfolio_id を返す（alembic seed の id=1 / Default）。"""
    resp = client.get("/portfolios")
    assert resp.status_code == 200
    portfolios = resp.json()
    assert len(portfolios) >= 1
    return portfolios[0]["portfolio_id"]


# ---------------------------------------------------------------------------
# funds マスタ
# ---------------------------------------------------------------------------


def test_post_funds_ok(client) -> None:
    """POST /funds 正常（isin+name+assoc_code）→ 201・FundOut（ADR-054）。"""
    resp = client.post(
        "/funds",
        json={"isin": ISIN_A, "name": "テスト投信A", "assoc_code": "0331234A"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["isin"] == ISIN_A
    assert body["name"] == "テスト投信A"
    assert body["assoc_code"] == "0331234A"

    # 一覧に出る
    listed = client.get("/funds").json()
    assert any(f["isin"] == ISIN_A for f in listed)


def test_post_funds_missing_assoc_code_422(client) -> None:
    """POST /funds で assoc_code 欠落 → 422（NAV 取得に必須・ADR-054）。"""
    resp = client.post("/funds", json={"isin": ISIN_A, "name": "テスト投信A"})
    assert resp.status_code == 422


def test_delete_fund_not_found_404(client) -> None:
    """DELETE /funds/{isin} 存在しない isin → 404（ADR-054）。"""
    resp = client.delete("/funds/JP90C000XXXX")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# fund_transactions（取引→導出）
# ---------------------------------------------------------------------------


def test_post_fund_transaction_returns_holdings_array(client) -> None:
    """POST /fund-transactions（buy）→ 201・FundHolding[]、口数・avg_cost が反映（ADR-054）。"""
    pid = _seed_portfolio(client)
    client.post("/funds", json={"isin": ISIN_A, "name": "投信A", "assoc_code": "0331234A"})

    resp = client.post(
        "/fund-transactions",
        json={
            "portfolio_id": pid,
            "isin": ISIN_A,
            "side": "buy",
            "units": 10_000,
            "price": 30_000,
            "traded_at": "2026-01-10",
        },
    )
    assert resp.status_code == 201
    holdings = resp.json()  # 素の配列（株式 transactions の dict ラップとは別契約）
    assert isinstance(holdings, list)
    h = next(h for h in holdings if h["isin"] == ISIN_A)
    assert h["units"] == pytest.approx(10_000.0)
    assert h["avg_cost"] == pytest.approx(30_000.0)


def test_fund_transactions_moving_average_and_sell(client) -> None:
    """複数取引で units 合計・移動平均、sell で units 減・avg 据え置きを検証（ADR-054）。"""
    pid = _seed_portfolio(client)
    client.post("/funds", json={"isin": ISIN_A, "name": "投信A", "assoc_code": "0331234A"})

    client.post(
        "/fund-transactions",
        json={
            "portfolio_id": pid,
            "isin": ISIN_A,
            "side": "buy",
            "units": 10_000,
            "price": 30_000,
            "traded_at": "2026-01-10",
        },
    )
    resp = client.post(
        "/fund-transactions",
        json={
            "portfolio_id": pid,
            "isin": ISIN_A,
            "side": "buy",
            "units": 20_000,
            "price": 36_000,
            "traded_at": "2026-01-11",
        },
    )
    holdings = resp.json()
    h = next(h for h in holdings if h["isin"] == ISIN_A)
    assert h["units"] == pytest.approx(30_000.0)
    expected_avg = (10_000 * 30_000 + 20_000 * 36_000) / 30_000
    assert h["avg_cost"] == pytest.approx(expected_avg)

    # sell 5,000 → units 25,000、avg 据え置き
    resp = client.post(
        "/fund-transactions",
        json={
            "portfolio_id": pid,
            "isin": ISIN_A,
            "side": "sell",
            "units": 5_000,
            "price": 35_000,
            "traded_at": "2026-01-12",
        },
    )
    holdings = resp.json()
    h = next(h for h in holdings if h["isin"] == ISIN_A)
    assert h["units"] == pytest.approx(25_000.0)
    assert h["avg_cost"] == pytest.approx(expected_avg)


def test_fund_holdings_with_latest_nav(client) -> None:
    """GET /fund-holdings が最新 NAV で market_value/unrealized_pnl 付き（ADR-054）。"""
    pid = _seed_portfolio(client)
    client.post("/funds", json={"isin": ISIN_A, "name": "投信A", "assoc_code": "0331234A"})
    client.post(
        "/fund-transactions",
        json={
            "portfolio_id": pid,
            "isin": ISIN_A,
            "side": "buy",
            "units": 10_000,
            "price": 30_000,
            "traded_at": "2026-01-10",
        },
    )
    # NAV を直接焼く（ネットに出ない）
    repo.upsert_fund_navs(
        [
            {"isin": ISIN_A, "date": "2026-06-01", "nav": 35_000.0},
            {"isin": ISIN_A, "date": "2026-06-02", "nav": 38_000.0},  # 最新
        ]
    )

    resp = client.get("/fund-holdings", params={"portfolio_id": pid})
    assert resp.status_code == 200
    h = next(h for h in resp.json() if h["isin"] == ISIN_A)
    assert h["last_nav"] == pytest.approx(38_000.0)
    assert h["nav_date"] == "2026-06-02"
    assert h["market_value"] == pytest.approx(10_000.0 / 10_000.0 * 38_000.0)
    assert h["unrealized_pnl"] == pytest.approx(10_000.0 / 10_000.0 * (38_000.0 - 30_000.0))
    assert h["weight"] == pytest.approx(1.0)  # 投信内 1 銘柄なので 1.0


# ---------------------------------------------------------------------------
# /asset-overview に投信が合算される
# ---------------------------------------------------------------------------


def test_asset_overview_includes_fund_value(client) -> None:
    """/asset-overview が fund_value を返し total 合算・投資信託スライスを持つ（ADR-054）。"""
    pid = _seed_portfolio(client)
    client.post("/funds", json={"isin": ISIN_A, "name": "投信A", "assoc_code": "0331234A"})
    client.post(
        "/fund-transactions",
        json={
            "portfolio_id": pid,
            "isin": ISIN_A,
            "side": "buy",
            "units": 10_000,
            "price": 30_000,
            "traded_at": "2026-01-10",
        },
    )
    repo.upsert_fund_navs([{"isin": ISIN_A, "date": "2026-06-02", "nav": 38_000.0}])

    resp = client.get("/asset-overview")
    assert resp.status_code == 200
    body = resp.json()

    expected_fund_value = 10_000.0 / 10_000.0 * 38_000.0  # 38,000
    assert body["fund_value"] == pytest.approx(expected_fund_value)
    # total に合算されている
    assert body["total_value"] == pytest.approx(
        body["stock_value"] + body["cash_value"] + body["external_value"] + body["fund_value"]
    )
    # allocation に「投資信託」スライスがあり値が一致
    fund_slice = next(s for s in body["allocation"] if s["name"] == "投資信託")
    assert fund_slice["value"] == pytest.approx(expected_fund_value)
