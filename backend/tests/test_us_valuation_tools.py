"""米株 valuation Tool（get_us_valuation / screen_us_valuation）の handler 検証。

Phase 7(B-1)・ADR-039/048/055。test_valuation_tools.py（日本株）のミラー。一時 SQLite・ネットに
出ない（testing-strategy）。検証の芯:
- 市場/通貨が契約として明示される（market="US" / currency="USD"）。
- Tool は事実だけを返し **verdict（割安/割高の判定）を持たない**（ADR-014）。
- 未焼成 symbol は found=False（USD 明示は維持）。
- screen_us_valuation が gics_sector で絞り、各候補に指標が載る。
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.advisor.tools import handlers
from app.db import repo
from app.db.engine import get_engine

# verdict（判定）を Tool が漏らしていないかの監査キー（ADR-014）。
_VERDICT_KEYS = {"verdict", "is_cheap", "is_undervalued", "判定", "割安", "rating"}


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _stock(symbol: str, sector: str, is_etf: int = 0) -> dict:
    return {
        "symbol": symbol,
        "company_name": f"name {symbol}",
        "gics_sector": sector,
        "industry": "sub",
        "is_etf": is_etf,
        "updated_at": "2026-06-09T00:00:00+00:00",
    }


def _snap(symbol: str, per: float, pbr: float, mcap: float, dy: float) -> dict:
    return {
        "symbol": symbol,
        "as_of_date": "2026-06-08",
        "close": 100.0,
        "eps": 10.0,
        "bps": 50.0,
        "dividend_per_share": 3.0,
        "shares_net": mcap / 100.0,
        "per": per,
        "pbr": pbr,
        "market_cap": mcap,
        "dividend_yield": dy,
        "roe": 0.2,
        "operating_margin": 0.15,
        "net_margin": 0.1,
        "revenue_growth_yoy": None,
        "op_growth_yoy": None,
        "profit_growth_yoy": None,
        "eps_growth_yoy": None,
        "fin_disclosed_date": None,
        "updated_at": "2026-06-09T00:00:00+00:00",
    }


def _seed(_temp_db) -> None:
    """Technology 2 銘柄（安/高）＋ Energy 1 銘柄＋ ETF 1 銘柄を焼く。"""
    repo.upsert_us_stocks(
        [
            _stock("CHEAPT", "Technology"),
            _stock("RICHT", "Technology"),
            _stock("ENGY", "Energy"),
            _stock("ETFX", "Technology", is_etf=1),
        ]
    )
    repo.upsert_us_valuation_snapshots(
        [
            _snap("CHEAPT", per=8.0, pbr=0.8, mcap=500e8, dy=0.04),
            _snap("RICHT", per=25.0, pbr=3.0, mcap=2000e8, dy=0.01),
            _snap("ENGY", per=12.0, pbr=1.5, mcap=8000e8, dy=0.02),
            _snap("ETFX", per=15.0, pbr=1.0, mcap=300e8, dy=0.0),
        ]
    )


def test_get_us_valuation_returns_facts_with_usd_and_no_verdict(temp_db) -> None:
    _seed(temp_db)
    out = _run(handlers.handle_get_us_valuation({"symbol": "CHEAPT"}))

    assert out["found"] is True
    assert out["market"] == "US"
    assert out["currency"] == "USD"
    assert out["as_of"] == "2026-06-08"
    # 事実が載る（PER/PBR/ROE＋GICS 業種）
    assert out["per"] == 8.0
    assert out["pbr"] == 0.8
    assert out["roe"] == 0.2
    assert out["gics_sector"] == "Technology"
    # GICS 業種内で per 最安（pctile=0）
    assert out["gics_sector_pctile"] == 0.0
    # 判定（verdict）は Tool が持たない（解釈は LLM・ADR-014）
    assert _VERDICT_KEYS.isdisjoint(out.keys())


def test_get_us_valuation_not_found_keeps_usd(temp_db) -> None:
    """未焼成 symbol は found=False（market/currency は契約として維持）。"""
    _seed(temp_db)
    out = _run(handlers.handle_get_us_valuation({"symbol": "NOPE"}))
    assert out["found"] is False
    assert out["market"] == "US"
    assert out["currency"] == "USD"


def test_screen_us_valuation_filters_by_gics_sector_and_labels_usd(temp_db) -> None:
    _seed(temp_db)
    # Technology 内の普通株を per_max=15 で絞る → CHEAPT のみ（RICHT は per25・ETFX は ETF）。
    out = _run(
        handlers.handle_screen_us_valuation(
            {"gics_sector": "Technology", "exclude_etf": True, "per_max": 15.0}
        )
    )
    assert out["market"] == "US"
    assert out["currency"] == "USD"
    assert out["count"] == 1
    item = out["items"][0]
    assert item["symbol"] == "CHEAPT"
    assert item["gics_sector"] == "Technology"
    assert "per" in item and "roe" in item
    # 別 sector（Energy）は絞りに掛からない
    assert all(r["gics_sector"] == "Technology" for r in out["items"])


def test_screen_us_valuation_normalizes_formal_gics_name(temp_db) -> None:
    """#2: 正式 GICS 名 'Information Technology' も canonical 'Technology' に正規化して絞る。

    正規化しないと格納値 'Technology' と exact 一致せず黙って 0 件になり AI が誤結論する。
    """
    _seed(temp_db)
    out = _run(
        handlers.handle_screen_us_valuation(
            {"gics_sector": "Information Technology", "exclude_etf": True, "per_max": 15.0}
        )
    )
    assert out["count"] == 1  # 正規化されて CHEAPT が拾える（0 件にならない）
    assert out["items"][0]["symbol"] == "CHEAPT"


def test_screen_us_valuation_empty_when_threshold_strict(temp_db) -> None:
    """しきい値を厳しくすると 0 件（破壊的ゲートはコードに無い＝AI が criteria を渡す）。"""
    _seed(temp_db)
    out = _run(handlers.handle_screen_us_valuation({"per_max": 1.0}))
    assert out["market"] == "US"
    assert out["currency"] == "USD"
    assert out["count"] == 0


def test_get_us_valuation_relays_net_cash_and_derives_ratio(temp_db) -> None:
    """清原式ネットキャッシュ（ADR-079・US）。net_cash は焼いた事実、比率は read-time 導出。"""
    _seed(temp_db)  # CHEAPT の market_cap = 500e8 = 5.0e10
    with get_engine().begin() as conn:
        repo.update_us_valuation_receivables_inventory(conn, "CHEAPT", {"net_cash": 6.0e10})
    out = _run(handlers.handle_get_us_valuation({"symbol": "CHEAPT"}))
    assert out["net_cash"] == 6.0e10
    assert abs(out["net_cash_ratio"] - 6.0e10 / 5.0e10) < 1e-9  # 1.2
    assert _VERDICT_KEYS.isdisjoint(out.keys())


def test_screen_us_valuation_filters_by_net_cash_ratio(temp_db) -> None:
    """net_cash_ratio_min で米株も絞れる（read-time 導出列・ADR-079）。"""
    _seed(temp_db)
    with get_engine().begin() as conn:
        repo.update_us_valuation_receivables_inventory(conn, "CHEAPT", {"net_cash": 6.0e10})
    out = _run(handlers.handle_screen_us_valuation({"net_cash_ratio_min": 1.0}))
    assert out["count"] == 1
    assert out["items"][0]["symbol"] == "CHEAPT"
    assert abs(out["items"][0]["net_cash_ratio"] - 1.2) < 1e-9
    out2 = _run(handlers.handle_screen_us_valuation({"net_cash_ratio_min": 1.5}))
    assert out2["count"] == 0
