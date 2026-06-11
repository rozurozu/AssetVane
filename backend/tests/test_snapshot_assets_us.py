"""snapshot_assets ジョブの米株 us_stock_value 合算と total_value 反映の検証（ADR-057）。

担保:
  - us_holdings × us_daily_quotes × fx_rates で us_stock_value が正しく計算される。
  - total_value に us_stock_value が含まれる。
  - asset_snapshots の us_stock_value 列に焼かれる。
  - FX 未取得（fx_rates 空）時は us_stock_value=0 になる（捏造しない・ADR-014）。
  - 一時 SQLite で実行（本物の DB に触れない・testing-strategy）。
"""

from __future__ import annotations

import pytest

from app.batch.jobs import snapshot_assets
from app.db import repo
from app.db.engine import get_engine
from app.db.schema import portfolios

# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------


def _seed_us_stock(symbol: str, company_name: str = "Test Corp") -> None:
    """us_stocks に 1 行登録する（us_holdings の FK を通すため必須）。"""
    repo.upsert_us_stocks(
        [{"symbol": symbol, "company_name": company_name, "is_etf": 0, "updated_at": "t"}]
    )


def _seed_us_quote(symbol: str, date: str, close: float) -> None:
    """us_daily_quotes に close を 1 行登録する。"""
    with get_engine().begin() as conn:
        repo.upsert_us_daily_quotes(
            conn,
            [
                {
                    "symbol": symbol,
                    "date": date,
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "volume": 1.0,
                    "adj_close": close,
                }
            ],
        )


def _seed_us_holding(symbol: str, shares: float, avg_cost: float, avg_cost_jpy: float) -> None:
    """us_holdings に 1 行登録する（commit は begin() 内で完了）。"""
    with get_engine().begin() as conn:
        repo.upsert_us_holding(
            conn,
            {
                "symbol": symbol,
                "shares": shares,
                "avg_cost": avg_cost,
                "avg_cost_jpy": avg_cost_jpy,
            },
        )


def _seed_fx_rate(date: str, rate: float) -> None:
    """fx_rates に USDJPY 1 行登録する。"""
    repo.upsert_fx_rates([{"date": date, "pair": "USDJPY", "rate": rate}])


# ---------------------------------------------------------------------------
# テスト本体
# ---------------------------------------------------------------------------


def test_us_stock_value_included_in_total(temp_db) -> None:
    """us_holdings × close × fx_rate が us_stock_value に焼かれ total_value に加算される。

    AAPL: 10 株 × close=200 USD × 150 JPY/USD = 300,000 JPY。
    他の資産（JP株/cash/external/fund）は空 → total = 300,000。
    """
    _seed_us_stock("AAPL")
    _seed_us_quote("AAPL", "2026-06-10", 200.0)
    _seed_us_holding("AAPL", shares=10.0, avg_cost=180.0, avg_cost_jpy=180.0 * 150.0)
    _seed_fx_rate("2026-06-10", 150.0)

    result = snapshot_assets.run()

    assert result.ok is True

    with get_engine().connect() as conn:
        snaps = repo.get_asset_snapshots(conn, limit=1)

    assert snaps, "asset_snapshots に行が無い"
    snap = snaps[0]
    expected_us = 10.0 * 200.0 * 150.0  # 300,000
    assert snap["us_stock_value"] == pytest.approx(expected_us)
    assert snap["total_value"] == pytest.approx(expected_us)


def test_us_stock_value_zero_when_no_fx(temp_db) -> None:
    """FX レートが未取得（fx_rates 空）のとき us_stock_value=0 になる（捏造しない・ADR-014）。"""
    _seed_us_stock("MSFT")
    _seed_us_quote("MSFT", "2026-06-10", 300.0)
    _seed_us_holding("MSFT", shares=5.0, avg_cost=280.0, avg_cost_jpy=280.0 * 150.0)
    # fx_rates を空のまま（_seed_fx_rate 未呼び出し）

    result = snapshot_assets.run()

    assert result.ok is True
    with get_engine().connect() as conn:
        snaps = repo.get_asset_snapshots(conn, limit=1)

    snap = snaps[0]
    assert snap["us_stock_value"] == pytest.approx(0.0)
    # cash/JP株/外部資産もゼロなので total=0
    assert snap["total_value"] == pytest.approx(0.0)


def test_us_stock_value_combined_with_cash(temp_db) -> None:
    """us_stock_value と cash_value の両方が total_value に合算される。"""
    # 先頭ポートフォリオ＋現金
    with get_engine().begin() as conn:
        conn.execute(portfolios.insert().values(portfolio_id=1, name="Default"))
    repo.upsert_cash(500_000.0)  # 50 万

    _seed_us_stock("NVDA")
    _seed_us_quote("NVDA", "2026-06-10", 100.0)
    _seed_us_holding("NVDA", shares=20.0, avg_cost=90.0, avg_cost_jpy=90.0 * 148.0)
    _seed_fx_rate("2026-06-10", 148.0)

    result = snapshot_assets.run()

    assert result.ok is True
    with get_engine().connect() as conn:
        snaps = repo.get_asset_snapshots(conn, limit=1)

    snap = snaps[0]
    expected_us = 20.0 * 100.0 * 148.0  # 296,000
    expected_total = expected_us + 500_000.0
    assert snap["us_stock_value"] == pytest.approx(expected_us)
    assert snap["cash_value"] == pytest.approx(500_000.0)
    assert snap["total_value"] == pytest.approx(expected_total)


def test_snapshot_idempotent_reupsert(temp_db) -> None:
    """同じ日に 2 回 run() しても asset_snapshots が重複しない（ADR-002 冪等）。"""
    _seed_us_stock("TSLA")
    _seed_us_quote("TSLA", "2026-06-10", 250.0)
    _seed_us_holding("TSLA", shares=4.0, avg_cost=200.0, avg_cost_jpy=200.0 * 149.0)
    _seed_fx_rate("2026-06-10", 149.0)

    snapshot_assets.run()
    result2 = snapshot_assets.run()

    assert result2.ok is True
    with get_engine().connect() as conn:
        snaps = repo.get_asset_snapshots(conn, limit=10)

    # 同じ date の行は 1 行だけ
    assert len(snaps) == 1


def test_us_stock_value_no_holdings(temp_db) -> None:
    """us_holdings が空のとき us_stock_value=0 で ok=True（正常処理）。"""
    _seed_fx_rate("2026-06-10", 150.0)

    result = snapshot_assets.run()

    assert result.ok is True
    with get_engine().connect() as conn:
        snaps = repo.get_asset_snapshots(conn, limit=1)

    assert snaps[0]["us_stock_value"] == pytest.approx(0.0)
