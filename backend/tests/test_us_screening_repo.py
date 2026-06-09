"""米株スクリーニング repo（us_valuation_snapshots × us_stocks・ADR-031/039/048/055）のテスト。

担保: GICS sector 内パーセンタイル（gics_sector_pctile・per 昇順）・時価総額順位（market_cap_rank・
降順）・絶対レンジ/sort allowlist・exclude_etf・get_us_valuation_snapshot 単票。本物の DB に触れず
一時 SQLite（temp_db）で検証する（testing-strategy・ネットに出ない）。
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


def _seed(_temp_db) -> None:
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


def test_screen_absolute_range_and_join(temp_db) -> None:
    """絶対レンジ（per_max・dividend_yield_min）で絞り込み、us_stocks から名称/業種を JOIN 補完。"""
    _seed(temp_db)
    with get_engine().connect() as conn:
        rows = repo.screen_us_stocks(conn, {"per_max": 13.0, "dividend_yield_min": 0.015})
    syms = {r["symbol"] for r in rows}
    assert syms == {"CHEAPT", "ENGY"}  # per<=13 かつ 利回り>=1.5%
    row = next(r for r in rows if r["symbol"] == "CHEAPT")
    assert row["company_name"] == "name CHEAPT"
    assert row["gics_sector"] == "Technology"


def test_screen_exclude_etf_and_sector(temp_db) -> None:
    """gics_sector 完全一致 ＋ exclude_etf で ETF を除外（Technology の普通株のみ）。"""
    _seed(temp_db)
    with get_engine().connect() as conn:
        rows = repo.screen_us_stocks(conn, {"gics_sector": "Technology", "exclude_etf": True})
    assert {r["symbol"] for r in rows} == {"CHEAPT", "RICHT"}  # ETFX を除外


def test_gics_sector_pctile_partitions_by_sector(temp_db) -> None:
    """gics_sector_pctile は GICS sector 内 per 昇順 percent_rank（CHEAPT が業種内最安）。"""
    _seed(temp_db)
    with get_engine().connect() as conn:
        # Technology 内で per 最安（pctile<=0）→ CHEAPT のみ（RICHT は高い）。
        cheap = repo.screen_us_stocks(
            conn, {"gics_sector": "Technology", "gics_sector_pctile_max": 0.0}
        )
        snap = repo.get_us_valuation_snapshot(conn, "ENGY")
    assert [r["symbol"] for r in cheap] == ["CHEAPT"]
    # ENGY は Energy sector で 1 銘柄のみ＝percent_rank は 0（単独で最安扱い・GICS 内で算出）。
    assert snap is not None
    assert snap["gics_sector_pctile"] == 0.0
    assert snap["gics_sector"] == "Energy"


def test_market_cap_rank_descending(temp_db) -> None:
    """market_cap_rank は時価総額降順 row_number（上位 2 = ENGY 8000e8・RICHT 2000e8）。"""
    _seed(temp_db)
    with get_engine().connect() as conn:
        top2 = repo.screen_us_stocks(conn, {"market_cap_rank_max": 2})
    assert {r["symbol"] for r in top2} == {"ENGY", "RICHT"}


def test_sort_allowlist_falls_back_on_unknown_col(temp_db) -> None:
    """sort_by allowlist 外の列名は既定（market_cap 降順）に倒れる（インジェクション防止）。"""
    _seed(temp_db)
    with get_engine().connect() as conn:
        by_per = repo.screen_us_stocks(conn, {"sort_by": "per", "sort_dir": "asc", "limit": 2})
        bad = repo.screen_us_stocks(conn, {"sort_by": "DROP TABLE", "limit": 1})
    assert [r["symbol"] for r in by_per] == ["CHEAPT", "ENGY"]  # per 昇順 8,12
    assert bad[0]["symbol"] == "ENGY"  # 既定=時価総額降順の先頭（8000e8）


def test_get_us_valuation_snapshot_none_when_unbaked(temp_db) -> None:
    """未焼成 symbol は None（get_us_valuation_snapshot）。"""
    _seed(temp_db)
    with get_engine().connect() as conn:
        assert repo.get_us_valuation_snapshot(conn, "NOPE") is None
