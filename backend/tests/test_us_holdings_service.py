"""米株保有サービスの単体テスト（ADR-057・services/us_holdings.py）。

バグの震源地である 2 点を純粋ロジックとして固定する:
  1. recalc_us_holdings の avg_cost_jpy が「異なる約定 FX をまたぐ移動平均」を正しく畳むこと
     （avg_cost_jpy ≠ avg_cost(USD) × 現レート。約定時レートで固定した JPY 原価の平均である）。
  2. value_us_holdings の FX 換算・取得時レート固定原価・為替損益・None 安全（fx/終値欠損）。

DB は temp_db フィクスチャの一時 SQLite。FxAdapter（ネット）には出ない。
"""

from __future__ import annotations

from pytest import approx

from app.db import repo
from app.db.engine import get_engine
from app.services.us_holdings import recalc_us_holdings, value_us_holdings


def _seed_stock(symbol: str = "AAPL") -> None:
    """us_transactions の FK 親（us_stocks）を 1 件投入する。"""
    repo.upsert_us_stocks(
        [{"symbol": symbol, "company_name": "Apple", "gics_sector": "Technology", "is_etf": 0}]
    )


def _txn(
    symbol: str, side: str, shares: float, price: float, fx_rate: float, traded_at: str
) -> dict:
    return {
        "symbol": symbol,
        "side": side,
        "shares": shares,
        "price": price,
        "fee": None,
        "traded_at": traded_at,
        "fx_rate": fx_rate,
        "note": None,
    }


def test_recalc_avg_cost_jpy_cross_fx(temp_db) -> None:
    """異なる約定 FX をまたぐ buy の avg_cost(USD) と avg_cost_jpy が移動平均で導出される。

    buy 10@100(fx150) ＋ buy 10@200(fx160):
      avg_cost(USD)  = (10*100 + 10*200) / 20 = 150
      avg_cost_jpy   = (10*100*150 + 10*200*160) / 20 = (150000 + 320000)/20 = 23500
                       ※ avg_cost(150) × どの単一レートでもこの値にはならない（固定原価の平均）
    """
    _seed_stock()
    with get_engine().begin() as conn:
        repo.insert_us_transaction(conn, _txn("AAPL", "buy", 10, 100.0, 150.0, "2026-06-01"))
        repo.insert_us_transaction(conn, _txn("AAPL", "buy", 10, 200.0, 160.0, "2026-06-05"))
        recalc_us_holdings(conn, "AAPL")

    with get_engine().connect() as conn:
        rows = repo.list_us_holdings(conn)
    assert len(rows) == 1
    h = rows[0]
    assert h["shares"] == approx(20.0)
    assert h["avg_cost"] == approx(150.0)
    assert h["avg_cost_jpy"] == approx(23500.0)
    # avg_cost(USD) × 現レートでは固定原価平均を再現できないことを明示（FX 損益の素）。
    assert h["avg_cost_jpy"] != approx(150.0 * 160.0)


def test_recalc_partial_then_full_sell(temp_db) -> None:
    """部分売却は avg を変えず shares を減らし、全売却で保有行が消える（ADR-019）。"""
    _seed_stock()
    with get_engine().begin() as conn:
        repo.insert_us_transaction(conn, _txn("AAPL", "buy", 10, 100.0, 150.0, "2026-06-01"))
        repo.insert_us_transaction(conn, _txn("AAPL", "buy", 10, 200.0, 160.0, "2026-06-05"))
        repo.insert_us_transaction(conn, _txn("AAPL", "sell", 5, 250.0, 155.0, "2026-06-08"))
        recalc_us_holdings(conn, "AAPL")
    with get_engine().connect() as conn:
        h = repo.list_us_holdings(conn)[0]
    # 部分売却: shares 15・avg は不変（USD150 / JPY23500）
    assert h["shares"] == approx(15.0)
    assert h["avg_cost"] == approx(150.0)
    assert h["avg_cost_jpy"] == approx(23500.0)

    # 残り全部を売る → 保有行が消える
    with get_engine().begin() as conn:
        repo.insert_us_transaction(conn, _txn("AAPL", "sell", 15, 300.0, 158.0, "2026-06-10"))
        recalc_us_holdings(conn, "AAPL")
    with get_engine().connect() as conn:
        assert repo.list_us_holdings(conn) == []


def test_value_us_holdings_fx_gain_included() -> None:
    """評価額は現レート・原価は取得時レート固定 → 為替損益が含み損益に乗る（純関数・ADR-057）。"""
    rows = [{"symbol": "AAPL", "shares": 20.0, "avg_cost": 150.0, "avg_cost_jpy": 23500.0}]
    closes = {"AAPL": {"date": "2026-06-10", "close": 300.0}}
    # 現レート 170（取得時 150/160 より円安）→ 評価額は 170 で膨らみ、原価は固定 23500 のまま。
    v = value_us_holdings(rows, closes, fx_rate=170.0)[0]
    assert v["market_value_jpy"] == approx(20 * 300.0 * 170.0)  # 1,020,000
    assert v["cost_jpy"] == approx(20 * 23500.0)  # 470,000
    assert v["unrealized_pnl_jpy"] == approx(1_020_000.0 - 470_000.0)  # 550,000（為替益込み）
    assert v["weight"] == approx(1.0)
    assert v["last_close"] == approx(300.0)
    assert v["fx_rate"] == approx(170.0)


def test_value_us_holdings_none_safety() -> None:
    """fx 未取得・終値欠損では評価系が None になり捏造しない（ADR-014・fund 同方針）。"""
    rows = [{"symbol": "AAPL", "shares": 10.0, "avg_cost": 100.0, "avg_cost_jpy": 15000.0}]
    closes = {"AAPL": {"date": "2026-06-10", "close": 200.0}}

    # fx None → 評価系すべて None
    v_no_fx = value_us_holdings(rows, closes, fx_rate=None)[0]
    assert v_no_fx["market_value_jpy"] is None
    assert v_no_fx["cost_jpy"] is None
    assert v_no_fx["unrealized_pnl_jpy"] is None
    assert v_no_fx["weight"] is None

    # 終値欠損（latest_closes にエントリ無し）→ その銘柄は評価系 None
    v_no_close = value_us_holdings(rows, {}, fx_rate=150.0)[0]
    assert v_no_close["market_value_jpy"] is None
    assert v_no_close["unrealized_pnl_jpy"] is None


def test_value_us_holdings_weight_excludes_unpriced() -> None:
    """weight の分母（米株内合計）は終値の取れた銘柄だけで構成される（None 安全な按分）。"""
    rows = [
        {"symbol": "AAPL", "shares": 10.0, "avg_cost": 100.0, "avg_cost_jpy": 15000.0},
        {"symbol": "MSFT", "shares": 10.0, "avg_cost": 200.0, "avg_cost_jpy": 30000.0},
    ]
    # MSFT は終値欠損 → 分母は AAPL のみ → AAPL の weight=1.0、MSFT は None
    closes = {"AAPL": {"date": "2026-06-10", "close": 100.0}}
    valued = value_us_holdings(rows, closes, fx_rate=150.0)
    by_symbol = {v["symbol"]: v for v in valued}
    assert by_symbol["AAPL"]["weight"] == approx(1.0)
    assert by_symbol["MSFT"]["weight"] is None
