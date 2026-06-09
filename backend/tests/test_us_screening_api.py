"""米株 API（/us-stocks・/us-stocks/screen・/us-stocks/{symbol}・/us-quotes/{symbol}）のテスト。

担保: GICS sector 完全一致・絶対レンジ・exclude_etf・sort allowlist の絞り込み
（Phase 7(B-1)・ADR-031/039/055）、/us-stocks/{symbol} の 200（マスタ＋valuation）/未存在 404、
未焼成は valuation=null、/us-quotes/{symbol} の date 昇順。本物の DB に触れず
client（一時 SQLite＋alembic）で検証する（testing-strategy・ネットに出ない＝dict を INSERT）。
"""

from __future__ import annotations

from app.db import repo
from app.db.engine import get_engine


def _stock(symbol: str, sector: str, is_etf: int = 0) -> dict:
    return {
        "symbol": symbol,
        "company_name": f"name {symbol}",
        "gics_sector": sector,
        "industry": "sub",
        "is_etf": is_etf,
        "updated_at": "2026-06-09T00:00:00+00:00",
    }


def _snap(symbol: str, per, pbr, mcap, dy) -> dict:
    return {
        "symbol": symbol,
        "as_of_date": "2026-06-08",
        "close": 100.0,
        "eps": 10.0,
        "bps": 50.0,
        "dividend_per_share": 3.0,
        "shares_net": mcap / 100.0 if mcap else None,
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


def _seed_master_and_snaps() -> None:
    repo.upsert_us_stocks(
        [
            _stock("CHEAPT", "Technology"),  # Technology・PER 最安
            _stock("RICHT", "Technology"),  # Technology・PER 高
            _stock("ENGY", "Energy"),  # 別 sector
            _stock("ETFX", "Technology", is_etf=1),  # ETF
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


def _quote(symbol: str, date: str, close: float) -> dict:
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


def test_screen_absolute_range(client) -> None:
    """絶対レンジ（per_max・dividend_yield_min）で絞り込み、指標・ランク列を返す。"""
    _seed_master_and_snaps()
    rows = client.get(
        "/us-stocks/screen", params={"per_max": 13, "dividend_yield_min": 0.015}
    ).json()
    assert {r["symbol"] for r in rows} == {"CHEAPT", "ENGY"}
    a = next(r for r in rows if r["symbol"] == "CHEAPT")
    assert a["company_name"] == "name CHEAPT" and a["per"] == 8.0
    assert "gics_sector_pctile" in a and "market_cap_rank" in a


def test_screen_route_not_eaten_by_symbol(client) -> None:
    """/us-stocks/screen が /us-stocks/{symbol} に食われていない（200 を返す）。"""
    assert client.get("/us-stocks/screen").status_code == 200


def test_screen_sector_exclude_etf_and_sort(client) -> None:
    """gics_sector 完全一致＋exclude_etf で ETF を除外し、sort_by=per asc で並ぶ。"""
    _seed_master_and_snaps()
    rows = client.get(
        "/us-stocks/screen",
        params={
            "gics_sector": "Technology",
            "exclude_etf": True,
            "sort_by": "per",
            "sort_dir": "asc",
        },
    ).json()
    assert [r["symbol"] for r in rows] == ["CHEAPT", "RICHT"]  # ETFX を除外・per 昇順


def test_get_us_stock_detail_with_valuation(client) -> None:
    """/us-stocks/{symbol} はマスタ＋valuation snapshot を返す（焼成済み）。"""
    _seed_master_and_snaps()
    body = client.get("/us-stocks/ENGY").json()
    assert body["symbol"] == "ENGY"
    assert body["company_name"] == "name ENGY"
    assert body["valuation"] is not None
    assert body["valuation"]["per"] == 12.0
    assert body["valuation"]["gics_sector"] == "Energy"


def test_get_us_stock_detail_null_valuation_when_unbaked(client) -> None:
    """マスタはあるが valuation 未焼成なら 200＋valuation=null（/stocks/{code} 同型）。"""
    repo.upsert_us_stocks([_stock("BARE", "Technology")])  # snapshot を焼かない
    resp = client.get("/us-stocks/BARE")
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "BARE"
    assert body["valuation"] is None


def test_get_us_stock_404_when_missing(client) -> None:
    """未取得 symbol は 404。"""
    assert client.get("/us-stocks/NOPE").status_code == 404


def test_get_us_quotes_date_ascending(client) -> None:
    """/us-quotes/{symbol} は date 昇順（INSERT 順が逆でも昇順で返る）。"""
    with get_engine().begin() as conn:
        repo.upsert_us_daily_quotes(
            conn,
            [
                _quote("AAPL", "2026-06-03", 102.0),
                _quote("AAPL", "2026-06-01", 100.0),
                _quote("AAPL", "2026-06-02", 101.0),
            ],
        )
    rows = client.get("/us-quotes/AAPL").json()
    assert [r["date"] for r in rows] == ["2026-06-01", "2026-06-02", "2026-06-03"]
    assert rows[0]["close"] == 100.0
