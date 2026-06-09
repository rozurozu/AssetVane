"""services.us_valuation の組み立て検証（ADR-031/048/055）。一時 SQLite・ネットに出ない。

担保: us_stocks の財務素 × 最新 close から quant.valuation 純関数で per/pbr/roe/利益率を組むこと・
赤字/欠損で None・close 無し銘柄も行は作る（指標 None）・YoY は素が無いため全 None（ADR-055）。
quant 純関数（valuation.per/pbr/roe）と一致することで「別計算式を持ち込まない」ことを担保する。
"""

from __future__ import annotations

from app.db import repo
from app.db.engine import get_engine
from app.quant import valuation
from app.services import us_valuation as us_valsvc


def _quote(symbol: str, date: str, close: float) -> dict:
    return {
        "symbol": symbol,
        "date": date,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1.0,
        "adj_close": close,
    }


def test_build_uses_quant_pure_functions(temp_db) -> None:
    """per/pbr/roe が quant.valuation 純関数の出力と一致する（再利用の証跡・ADR-014/016）。"""
    repo.upsert_us_stocks(
        [
            {
                "symbol": "AAPL",
                "company_name": "Apple",
                "gics_sector": "Technology",
                "is_etf": 0,
                "eps": 6.0,
                "bps": 4.0,
                "shares_net": 1_000.0,
                "dividend_per_share": 1.0,
                "net_sales": 400.0,
                "operating_profit": 120.0,
                "profit": 100.0,
                "updated_at": "t",
            }
        ]
    )
    # 最新 close は 2026-06-08（古い日も入れて MAX 採用を確認）。
    with get_engine().begin() as conn:
        repo.upsert_us_daily_quotes(
            conn, [_quote("AAPL", "2026-05-01", 150.0), _quote("AAPL", "2026-06-08", 180.0)]
        )

    with get_engine().connect() as conn:
        rows = us_valsvc.build_us_valuation_snapshots(conn)

    r = next(x for x in rows if x["symbol"] == "AAPL")
    assert r["as_of_date"] == "2026-06-08"
    assert r["close"] == 180.0
    # quant 純関数と一致（別計算式を持ち込まない）。
    assert r["per"] == valuation.per(180.0, 6.0)
    assert r["pbr"] == valuation.pbr(180.0, 4.0)
    assert r["roe"] == valuation.roe(6.0, 4.0)
    assert r["operating_margin"] == valuation.operating_margin(120.0, 400.0)
    assert r["net_margin"] == valuation.net_margin(100.0, 400.0)
    assert r["market_cap"] == valuation.market_cap(180.0, 1_000.0)
    assert r["dividend_yield"] == valuation.dividend_yield(1.0, 180.0)
    # YoY 中継列を入れていないので全 None（op/eps は常に None・ADR-055）。
    assert r["revenue_growth_yoy"] is None
    assert r["profit_growth_yoy"] is None
    assert r["op_growth_yoy"] is None
    assert r["eps_growth_yoy"] is None


def test_build_transcribes_yoy_from_us_stocks_relay_columns(temp_db) -> None:
    """YoY 中継列を厳密転記する: revenue←revenue・profit←earnings・op/eps は None（ADR-055）。"""
    repo.upsert_us_stocks(
        [
            {
                "symbol": "MSFT",
                "company_name": "Microsoft",
                "gics_sector": "Technology",
                "is_etf": 0,
                "eps": 11.0,
                "bps": 30.0,
                # `.info` 提供の YoY 率（実値）。revenueGrowth=売上・earningsGrowth=純利益。
                "revenue_growth_yoy": 0.16,
                "earnings_growth_yoy": 0.22,
                "updated_at": "t",
            }
        ]
    )
    with get_engine().begin() as conn:
        repo.upsert_us_daily_quotes(conn, [_quote("MSFT", "2026-06-08", 400.0)])
    with get_engine().connect() as conn:
        rows = us_valsvc.build_us_valuation_snapshots(conn)

    r = next(x for x in rows if x["symbol"] == "MSFT")
    # 売上 YoY ← revenue_growth_yoy（厳密一致・別計算式を作らない）。
    assert r["revenue_growth_yoy"] == 0.16
    # 純利益 YoY ← earnings_growth_yoy（earningsGrowth は純利益成長＝profit 軸へ）。
    assert r["profit_growth_yoy"] == 0.22
    # 営業利益 YoY・EPS YoY は `.info` に素が無いため None（捏造しない）。
    assert r["op_growth_yoy"] is None
    assert r["eps_growth_yoy"] is None


def test_build_none_metrics_for_loss_and_missing(temp_db) -> None:
    """赤字（eps<=0）・欠損（bps None）で per/pbr が None になる（捏造しない・ADR-014）。"""
    repo.upsert_us_stocks(
        [
            {
                "symbol": "LOSS",
                "company_name": "Loss Co",
                "gics_sector": "Energy",
                "is_etf": 0,
                "eps": -2.0,  # 赤字 → PER None
                "bps": None,  # 欠損 → PBR None
                "updated_at": "t",
            }
        ]
    )
    with get_engine().begin() as conn:
        repo.upsert_us_daily_quotes(conn, [_quote("LOSS", "2026-06-08", 50.0)])
    with get_engine().connect() as conn:
        rows = us_valsvc.build_us_valuation_snapshots(conn)
    r = next(x for x in rows if x["symbol"] == "LOSS")
    assert r["per"] is None
    assert r["pbr"] is None


def test_build_makes_row_without_close(temp_db) -> None:
    """close 無し銘柄（OHLCV 未取得）も行は作る・指標は None（網羅性・ADR-031）。"""
    repo.upsert_us_stocks(
        [
            {
                "symbol": "NEW",
                "company_name": "Newly listed",
                "gics_sector": "Health Care",
                "is_etf": 0,
                "eps": 5.0,
                "bps": 10.0,
                "shares_net": 100.0,
                "updated_at": "t",
            }
        ]
    )
    with get_engine().connect() as conn:
        rows = us_valsvc.build_us_valuation_snapshots(conn)
    r = next(x for x in rows if x["symbol"] == "NEW")
    assert r["close"] is None
    assert r["per"] is None and r["pbr"] is None and r["market_cap"] is None
    # close 無しでも as_of_date は NOT NULL 制約を満たす（焼成日が入る）。
    assert r["as_of_date"] is not None
