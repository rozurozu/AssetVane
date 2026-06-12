"""米株保有・取引 API（/us-holdings・/us-transactions）のテスト。

担保（Phase 7(B-2)・ADR-057）:
- POST /us-transactions（AAPL buy）→ GET /us-holdings で JPY 評価額が返る。
- fx_rate 未指定＋fx_rates 空 → 400。
- PUT /us-transactions/{id} で編集 → us_holdings 再計算（C-14＝tasks/review-2026-06-12.md・
  JP test_put_transaction_recalcs_holdings のミラー）。存在しない id は 404。
- DELETE /us-transactions/{id} で全売却 → us_holdings から消える。
- GET /asset-overview に「米国株」スライスが乗り total に合算。
- AI Tool handle_get_us_holdings が JPY 評価を返す。

一時 SQLite（client フィクスチャ＝alembic 経路）で検証。ネットに出ない。
us_stocks（FK 親）と fx_rates を必ず先に投入する。
"""

from __future__ import annotations

import asyncio
from typing import Any

from pytest import approx as pytest_approx

from app.advisor.tools import handlers
from app.db import repo
from app.db.engine import get_engine

# ---------------------------------------------------------------------------
# ヘルパ
# ---------------------------------------------------------------------------


def _us_stock(symbol: str) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "company_name": f"{symbol} Inc.",
        "gics_sector": "Technology",
        "industry": "Software",
        "is_etf": 0,
        "updated_at": "2026-06-11T00:00:00+00:00",
    }


def _fx_rate(date: str, rate: float) -> dict[str, Any]:
    return {"date": date, "pair": "USDJPY", "rate": rate}


def _us_quote(symbol: str, date: str, close: float) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "date": date,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1000.0,
        "adj_close": close,
    }


def _seed_master(symbol: str = "AAPL") -> None:
    """us_stocks マスタ（FK 親）を投入する。"""
    repo.upsert_us_stocks([_us_stock(symbol)])


def _seed_fx(rate: float = 155.0, date: str = "2026-06-11") -> None:
    """USDJPY FX レートを投入する。"""
    repo.upsert_fx_rates([_fx_rate(date, rate)])


def _seed_quote(symbol: str = "AAPL", close: float = 200.0) -> None:
    """最新終値（us_daily_quotes）を投入する。"""
    with get_engine().begin() as conn:
        repo.upsert_us_daily_quotes(conn, [_us_quote(symbol, "2026-06-11", close)])


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# POST /us-transactions → GET /us-holdings（JPY 評価が返る）
# ---------------------------------------------------------------------------


def test_post_us_transaction_returns_holdings_with_jpy(client) -> None:
    """AAPL buy → holdings に JPY 評価額が付く。"""
    _seed_master("AAPL")
    _seed_fx(155.0)
    _seed_quote("AAPL", 200.0)

    resp = client.post(
        "/us-transactions",
        json={
            "symbol": "AAPL",
            "side": "buy",
            "shares": 10.0,
            "price": 190.0,
            "traded_at": "2026-06-10",
            "fx_rate": 154.0,
        },
    )
    assert resp.status_code == 201
    holdings = resp.json()
    assert len(holdings) == 1
    h = holdings[0]
    assert h["symbol"] == "AAPL"
    assert h["shares"] == 10.0
    # market_value_jpy = 10 × 200 × 155 = 310000
    assert h["market_value_jpy"] == pytest_approx(310_000.0)
    # cost_jpy = 10 × (190 × 154) = 10 × 29260 = 292600
    assert h["cost_jpy"] == pytest_approx(292_600.0)
    # unrealized_pnl_jpy = 310000 - 292600 = 17400
    assert h["unrealized_pnl_jpy"] == pytest_approx(17_400.0)
    # weight は米株内で 1.0（保有が 1 銘柄）
    assert h["weight"] == pytest_approx(1.0)
    # 返却値に fx_rate が乗る
    assert h["fx_rate"] == pytest_approx(155.0)


def test_get_us_holdings_returns_holdings(client) -> None:
    """GET /us-holdings は保有一覧を返す。"""
    _seed_master("AAPL")
    _seed_fx(155.0)
    _seed_quote("AAPL", 200.0)
    client.post(
        "/us-transactions",
        json={
            "symbol": "AAPL",
            "side": "buy",
            "shares": 5.0,
            "price": 190.0,
            "traded_at": "2026-06-10",
            "fx_rate": 154.0,
        },
    )

    resp = client.get("/us-holdings")
    assert resp.status_code == 200
    holdings = resp.json()
    assert len(holdings) == 1
    assert holdings[0]["symbol"] == "AAPL"


# ---------------------------------------------------------------------------
# fx_rate 未指定＋fx_rates 空 → 400
# ---------------------------------------------------------------------------


def test_post_us_transaction_returns_400_when_no_fx(client) -> None:
    """fx_rate 省略かつ fx_rates が空のとき 400 を返す。"""
    _seed_master("AAPL")
    # fx_rates を投入しない

    resp = client.post(
        "/us-transactions",
        json={
            "symbol": "AAPL",
            "side": "buy",
            "shares": 1.0,
            "price": 190.0,
            "traded_at": "2026-06-10",
            # fx_rate を渡さない
        },
    )
    assert resp.status_code == 400
    assert "FX" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# PUT /us-transactions/{id}（編集 → us_holdings 再導出・C-14）
# ---------------------------------------------------------------------------


def test_put_us_transaction_recalcs_holdings(client) -> None:
    """PUT で株数・単価・fee・fx_rate を変更すると us_holdings が再計算される（ADR-057/019）。"""
    _seed_master("AAPL")
    _seed_fx(155.0)
    _seed_quote("AAPL", 200.0)

    create = client.post(
        "/us-transactions",
        json={
            "symbol": "AAPL",
            "side": "buy",
            "shares": 10.0,
            "price": 190.0,
            "traded_at": "2026-06-10",
            "fx_rate": 154.0,
        },
    )
    assert create.status_code == 201
    txn_id = client.get("/us-transactions").json()[0]["id"]

    # 株数 20・単価 195・fee 1.5・fx_rate 150 に編集
    resp = client.put(
        f"/us-transactions/{txn_id}",
        json={
            "symbol": "AAPL",
            "side": "buy",
            "shares": 20.0,
            "price": 195.0,
            "fee": 1.5,
            "traded_at": "2026-06-10",
            "fx_rate": 150.0,
        },
    )
    assert resp.status_code == 200
    holdings = resp.json()
    assert len(holdings) == 1
    h = holdings[0]
    assert h["shares"] == 20.0
    assert h["avg_cost"] == pytest_approx(195.0)
    # avg_cost_jpy = 195 × 150 = 29250 → cost_jpy = 20 × 29250 = 585000
    assert h["cost_jpy"] == pytest_approx(585_000.0)

    # 取引履歴にも編集が反映される（fee / fx_rate）
    txn = client.get("/us-transactions").json()[0]
    assert txn["shares"] == 20.0
    assert txn["fee"] == pytest_approx(1.5)
    assert txn["fx_rate"] == pytest_approx(150.0)


def test_put_us_transaction_404_when_not_found(client) -> None:
    """存在しない取引 id への PUT は 404（C-14・JP test_put_transaction_404 のミラー）。"""
    _seed_master("AAPL")
    _seed_fx(155.0)
    resp = client.put(
        "/us-transactions/99999",
        json={
            "symbol": "AAPL",
            "side": "buy",
            "shares": 1.0,
            "price": 190.0,
            "traded_at": "2026-06-10",
            "fx_rate": 154.0,
        },
    )
    assert resp.status_code == 404


def test_put_us_transaction_symbol_change_recalcs_both(client) -> None:
    """symbol を変える PUT は旧 symbol 側の保有も再導出する（米株 recalc は symbol 単位・C-14）。"""
    _seed_master("AAPL")
    _seed_master("MSFT")
    _seed_fx(155.0)

    client.post(
        "/us-transactions",
        json={
            "symbol": "AAPL",
            "side": "buy",
            "shares": 5.0,
            "price": 190.0,
            "traded_at": "2026-06-10",
            "fx_rate": 154.0,
        },
    )
    txn_id = client.get("/us-transactions").json()[0]["id"]

    resp = client.put(
        f"/us-transactions/{txn_id}",
        json={
            "symbol": "MSFT",
            "side": "buy",
            "shares": 5.0,
            "price": 400.0,
            "traded_at": "2026-06-10",
            "fx_rate": 154.0,
        },
    )
    assert resp.status_code == 200
    holdings = resp.json()
    # 旧 AAPL の保有行は消え、MSFT のみ残る
    assert [h["symbol"] for h in holdings] == ["MSFT"]


def test_put_us_transaction_400_when_no_fx(client) -> None:
    """fx_rate 省略かつ fx_rates が空のとき PUT も 400（POST と同じ解決順・C-14）。"""
    _seed_master("AAPL")
    _seed_fx(155.0, date="2026-06-10")

    client.post(
        "/us-transactions",
        json={
            "symbol": "AAPL",
            "side": "buy",
            "shares": 1.0,
            "price": 190.0,
            "traded_at": "2026-06-10",
            "fx_rate": 154.0,
        },
    )
    txn_id = client.get("/us-transactions").json()[0]["id"]

    # 約定日を FX 未取得日（過去側）に変えつつ fx_rate を省略 → 400
    resp = client.put(
        f"/us-transactions/{txn_id}",
        json={
            "symbol": "AAPL",
            "side": "buy",
            "shares": 1.0,
            "price": 190.0,
            "traded_at": "2026-06-01",
            # fx_rate を渡さない
        },
    )
    assert resp.status_code == 400
    assert "FX" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# DELETE /us-transactions/{id} で全売却 → holdings から消える
# ---------------------------------------------------------------------------


def test_delete_us_transaction_removes_holding_on_full_sell(client) -> None:
    """buy 取引を削除（＝全売却相当）すると us_holdings から消える。"""
    _seed_master("AAPL")
    _seed_fx(155.0)

    post_resp = client.post(
        "/us-transactions",
        json={
            "symbol": "AAPL",
            "side": "buy",
            "shares": 3.0,
            "price": 190.0,
            "traded_at": "2026-06-10",
            "fx_rate": 154.0,
        },
    )
    assert post_resp.status_code == 201
    # 取引 id は GET /us-transactions から取る
    txns_resp = client.get("/us-transactions")
    assert txns_resp.status_code == 200
    txn_id = txns_resp.json()[0]["id"]

    del_resp = client.delete(f"/us-transactions/{txn_id}")
    assert del_resp.status_code == 200
    # 全売却相当 → holdings 空
    assert del_resp.json() == []

    # GET /us-holdings も空
    holdings_resp = client.get("/us-holdings")
    assert holdings_resp.json() == []


def test_delete_us_transaction_404_when_not_found(client) -> None:
    """存在しない取引 id は 404。"""
    resp = client.delete("/us-transactions/99999")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /asset-overview に「米国株」スライスが乗る
# ---------------------------------------------------------------------------


def test_asset_overview_includes_us_stock_slice(client) -> None:
    """asset-overview の allocation に「米国株」スライスが乗り total に合算される。"""
    _seed_master("AAPL")
    _seed_fx(155.0)
    _seed_quote("AAPL", 200.0)
    client.post(
        "/us-transactions",
        json={
            "symbol": "AAPL",
            "side": "buy",
            "shares": 10.0,
            "price": 190.0,
            "traded_at": "2026-06-10",
            "fx_rate": 154.0,
        },
    )

    resp = client.get("/asset-overview")
    assert resp.status_code == 200
    body = resp.json()

    # us_stock_value フィールドが存在する
    assert "us_stock_value" in body
    # 米株評価額 = 10 × 200 × 155 = 310000
    assert body["us_stock_value"] == pytest_approx(310_000.0)
    # total_value に米株が含まれる（現金 0 + 株式 0 + 米株 310000 = 310000 以上）
    assert body["total_value"] >= 310_000.0

    # allocation に「米国株」スライスが入る
    names = [s["name"] for s in body["allocation"]]
    assert "米国株" in names
    us_slice = next(s for s in body["allocation"] if s["name"] == "米国株")
    assert us_slice["value"] == pytest_approx(310_000.0)


# ---------------------------------------------------------------------------
# AI Tool handle_get_us_holdings が JPY 評価を返す
# ---------------------------------------------------------------------------


def test_handle_get_us_holdings_returns_jpy_valuation(temp_db) -> None:
    """handle_get_us_holdings が米株保有の JPY 評価事実を返す（ADR-057/014）。"""
    repo.upsert_us_stocks([_us_stock("MSFT")])
    repo.upsert_fx_rates([_fx_rate("2026-06-11", 155.0)])
    with get_engine().begin() as conn:
        repo.upsert_us_daily_quotes(conn, [_us_quote("MSFT", "2026-06-11", 400.0)])
        # holdings を直接挿入（recalc_us_holdings の代わりに手動挿入）
        repo.upsert_us_holding(
            conn,
            {
                "symbol": "MSFT",
                "shares": 5.0,
                "avg_cost": 380.0,
                "avg_cost_jpy": 380.0 * 154.0,  # 取得時レート
            },
        )

    out = _run(handlers.handle_get_us_holdings({}))

    assert "error" not in out
    assert len(out["holdings"]) == 1
    h = out["holdings"][0]
    assert h["symbol"] == "MSFT"
    # market_value_jpy = 5 × 400 × 155 = 310000
    assert h["market_value_jpy"] == pytest_approx(310_000.0)
    # cost_jpy = 5 × (380 × 154) = 5 × 58520 = 292600
    assert h["cost_jpy"] == pytest_approx(292_600.0)
    # unrealized_pnl_jpy = 310000 - 292600 = 17400
    assert h["unrealized_pnl_jpy"] == pytest_approx(17_400.0)
    # fx_rate が返却に含まれる
    assert out["fx_rate"] == pytest_approx(155.0)
    # verdict / 判定 は持たない（ADR-014）
    verdict_keys = {"verdict", "is_cheap", "is_undervalued", "判定", "割安"}
    assert verdict_keys.isdisjoint(h.keys())


def test_handle_get_us_holdings_empty_when_no_holdings(temp_db) -> None:
    """保有 0 のとき holdings は空リスト（エラーにならない）。"""
    out = _run(handlers.handle_get_us_holdings({}))
    assert "error" not in out
    assert out["holdings"] == []
