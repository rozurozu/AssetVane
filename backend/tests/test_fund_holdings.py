"""投信 holdings 再計算・評価（services/fund_holdings.py）の単体テスト（ADR-054・ADR-019）。

移動平均（recompute_positions を units/price で共有・recalc_fund_holdings 経由）と、
10,000 口換算の評価額・含み損益（value_fund_holdings）を手計算値と一致で検証する。
本物の DB に触れず一時 SQLite で回す（testing-strategy）。
"""

from __future__ import annotations

import pytest

from app.db import repo
from app.services.fund_holdings import (
    FUND_UNIT_BASE,
    recalc_fund_holdings,
    value_fund_holdings,
)
from app.services.holdings import recompute_positions

ISIN_A = "JP90C000ABC1"
ISIN_B = "JP90C000DEF2"


# ---------------------------------------------------------------------------
# 移動平均（recompute_positions を投信キーで呼ぶ・最重要）
# ---------------------------------------------------------------------------


def test_two_buys_moving_average_units() -> None:
    """投信 buy を 2 回積み増し: units 合計・avg_cost が加重移動平均になる（ADR-054）。

    10,000 口@30,000 → avg=30,000、その後 20,000 口@36,000 →
    avg = (10000*30000 + 20000*36000) / 30000 = 34,000。
    """
    # recompute_positions は traded_at を見ない。テストは price/units のみ与える。
    txns = [
        {"isin": ISIN_A, "side": "buy", "units": 10_000, "price": 30_000},
        {"isin": ISIN_A, "side": "buy", "units": 20_000, "price": 36_000},
    ]
    state = recompute_positions(txns, key_col="isin", qty_key="units", price_key="price")

    assert state[ISIN_A]["qty"] == pytest.approx(30_000.0)
    expected_avg = (10_000 * 30_000 + 20_000 * 36_000) / 30_000
    assert state[ISIN_A]["avg_cost"] == pytest.approx(expected_avg)


def test_sell_reduces_units_keeps_avg() -> None:
    """sell は units を減らし avg_cost は据え置き（ADR-054・株式 holdings と同方針）。"""
    txns = [
        {"isin": ISIN_A, "side": "buy", "units": 10_000, "price": 30_000},
        {"isin": ISIN_A, "side": "sell", "units": 4_000, "price": 33_000},
    ]
    state = recompute_positions(txns, key_col="isin", qty_key="units", price_key="price")

    assert state[ISIN_A]["qty"] == pytest.approx(6_000.0)
    assert state[ISIN_A]["avg_cost"] == pytest.approx(30_000.0)  # avg は buy 時の値のまま


def test_fee_not_in_avg_cost() -> None:
    """fee（手数料）は avg_cost に含めない（ADR-054・約定 price のみで導出）。

    fee を付けても付けなくても avg_cost は約定 price と一致する。
    """
    txns = [
        {
            "isin": ISIN_A,
            "side": "buy",
            "units": 10_000,
            "price": 30_000,
            "fee": 550,
            "traded_at": "2026-01-10",
        },
    ]
    state = recompute_positions(txns, key_col="isin", qty_key="units", price_key="price")

    assert state[ISIN_A]["avg_cost"] == pytest.approx(30_000.0)


def test_recalc_fund_holdings_directly(temp_db) -> None:
    """recalc_fund_holdings を直接呼んで repo から確認する（API 経由でない経路・ADR-019）。

    buy/sell を temp_db に直接入れて recalc → list_fund_holdings で確認。
    全売却の銘柄は holdings 行が消えること（units<=0 は保存しない）も同時に検証する。
    """
    from app.db.engine import get_engine
    from app.db.schema import portfolios

    repo.upsert_fund(ISIN_A, "テスト投信A", "0331234A")
    repo.upsert_fund(ISIN_B, "テスト投信B", "0331234B")

    with get_engine().begin() as conn:
        conn.execute(portfolios.insert().values(portfolio_id=1, name="Default"))

    with get_engine().begin() as conn:
        # ISIN_A: 10,000口@30,000 → 20,000口@36,000、その後 5,000口 売却
        repo.insert_fund_transaction(
            conn,
            {
                "portfolio_id": 1,
                "isin": ISIN_A,
                "side": "buy",
                "units": 10_000,
                "price": 30_000,
                "traded_at": "2026-01-05",
            },
        )
        repo.insert_fund_transaction(
            conn,
            {
                "portfolio_id": 1,
                "isin": ISIN_A,
                "side": "buy",
                "units": 20_000,
                "price": 36_000,
                "traded_at": "2026-01-06",
            },
        )
        repo.insert_fund_transaction(
            conn,
            {
                "portfolio_id": 1,
                "isin": ISIN_A,
                "side": "sell",
                "units": 5_000,
                "price": 35_000,
                "traded_at": "2026-01-07",
            },
        )
        # ISIN_B: 8,000口 買い→全売却 → holdings 行が消える
        repo.insert_fund_transaction(
            conn,
            {
                "portfolio_id": 1,
                "isin": ISIN_B,
                "side": "buy",
                "units": 8_000,
                "price": 12_000,
                "traded_at": "2026-01-05",
            },
        )
        repo.insert_fund_transaction(
            conn,
            {
                "portfolio_id": 1,
                "isin": ISIN_B,
                "side": "sell",
                "units": 8_000,
                "price": 12_500,
                "traded_at": "2026-01-08",
            },
        )
        recalc_fund_holdings(conn, 1)

    with get_engine().connect() as conn:
        holdings = repo.list_fund_holdings(conn, 1)

    # 全売却の ISIN_B は消え、ISIN_A だけ残る
    assert len(holdings) == 1
    h = holdings[0]
    assert h["isin"] == ISIN_A
    assert h["units"] == pytest.approx(25_000.0)  # 30,000 - 5,000
    expected_avg = (10_000 * 30_000 + 20_000 * 36_000) / 30_000
    assert h["avg_cost"] == pytest.approx(expected_avg)  # sell では変わらない


# ---------------------------------------------------------------------------
# 評価額・含み損益（10,000 口換算・value_fund_holdings）
# ---------------------------------------------------------------------------


def test_value_fund_holdings_market_value_and_pnl() -> None:
    """既知の units/avg_cost/nav で market_value・unrealized_pnl が手計算と一致（ADR-054）。

    market_value   = units/10000 * nav
    unrealized_pnl = units/10000 * (nav - avg_cost)
    weight は投信内合計に対する比率。
    """
    holdings_rows = [
        {"isin": ISIN_A, "name": "投信A", "units": 25_000.0, "avg_cost": 34_000.0},
        {"isin": ISIN_B, "name": "投信B", "units": 10_000.0, "avg_cost": 12_000.0},
    ]
    latest_navs = {
        ISIN_A: {"date": "2026-06-02", "nav": 38_000.0},
        ISIN_B: {"date": "2026-06-02", "nav": 11_000.0},
    }

    valued = value_fund_holdings(holdings_rows, latest_navs)
    by_isin = {v["isin"]: v for v in valued}

    a = by_isin[ISIN_A]
    mv_a = 25_000.0 / FUND_UNIT_BASE * 38_000.0  # 95,000
    pnl_a = 25_000.0 / FUND_UNIT_BASE * (38_000.0 - 34_000.0)  # 10,000
    assert a["market_value"] == pytest.approx(mv_a)
    assert a["unrealized_pnl"] == pytest.approx(pnl_a)
    assert a["last_nav"] == pytest.approx(38_000.0)
    assert a["nav_date"] == "2026-06-02"

    b = by_isin[ISIN_B]
    mv_b = 10_000.0 / FUND_UNIT_BASE * 11_000.0  # 11,000
    pnl_b = 10_000.0 / FUND_UNIT_BASE * (11_000.0 - 12_000.0)  # -1,000（含み損）
    assert b["market_value"] == pytest.approx(mv_b)
    assert b["unrealized_pnl"] == pytest.approx(pnl_b)

    # weight は投信内合計（mv_a + mv_b）に対する比率
    total = mv_a + mv_b
    assert a["weight"] == pytest.approx(mv_a / total)
    assert b["weight"] == pytest.approx(mv_b / total)
    assert a["weight"] + b["weight"] == pytest.approx(1.0)


def test_value_fund_holdings_missing_nav_is_none() -> None:
    """nav 不明な銘柄は評価関連列がすべて None（value_holdings と同方針・ADR-054）。"""
    holdings_rows = [
        {"isin": ISIN_A, "name": "投信A", "units": 25_000.0, "avg_cost": 34_000.0},
        {"isin": ISIN_B, "name": "投信B", "units": 10_000.0, "avg_cost": 12_000.0},
    ]
    # ISIN_B の nav は欠落
    latest_navs = {ISIN_A: {"date": "2026-06-02", "nav": 38_000.0}}

    valued = value_fund_holdings(holdings_rows, latest_navs)
    by_isin = {v["isin"]: v for v in valued}

    b = by_isin[ISIN_B]
    assert b["last_nav"] is None
    assert b["nav_date"] is None
    assert b["market_value"] is None
    assert b["unrealized_pnl"] is None
    assert b["weight"] is None

    # nav のある ISIN_A の weight は分母が自分だけになるため 1.0
    a = by_isin[ISIN_A]
    assert a["weight"] == pytest.approx(1.0)


def test_value_fund_holdings_avg_cost_none_keeps_market_value() -> None:
    """avg_cost が None でも market_value は出るが unrealized_pnl は None（ADR-054）。"""
    holdings_rows = [
        {"isin": ISIN_A, "name": "投信A", "units": 25_000.0, "avg_cost": None},
    ]
    latest_navs = {ISIN_A: {"date": "2026-06-02", "nav": 38_000.0}}

    valued = value_fund_holdings(holdings_rows, latest_navs)
    a = valued[0]

    assert a["market_value"] == pytest.approx(25_000.0 / FUND_UNIT_BASE * 38_000.0)
    assert a["unrealized_pnl"] is None
