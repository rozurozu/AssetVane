"""スクリーニング repo（valuation_snapshots / screening_filters・ADR-031）のテスト。

本物の DB に触れず一時 SQLite（temp_db）で検証する（testing-strategy）。
"""

from __future__ import annotations

from app.db import repo
from app.db.engine import get_engine


def _stock(code: str, name: str, sector: str, is_etf: int = 0) -> dict:
    return {
        "code": code,
        "company_name": name,
        "sector33_code": sector,
        "sector17_code": "6",
        "market_code": "0111",
        "is_etf": is_etf,
        "updated_at": "2026-06-04T00:00:00+00:00",
    }


def _fin(code: str, date: str, period: str, **kw) -> dict:
    base = {
        "code": code,
        "disclosed_date": date,
        "fiscal_period": period,
        "net_sales": None,
        "operating_profit": None,
        "profit": None,
        "eps": None,
        "bps": None,
        "dividend_per_share": None,
        "shares_outstanding": None,
        "treasury_shares": None,
    }
    base.update(kw)
    return base


def test_latest_financials_pick_fy_for_bps_and_latest_for_dividend(temp_db) -> None:
    """最新FY行から eps/bps、最新行から配当/株数を拾う（四半期は bps 空＝実機の形）。"""
    repo.upsert_stocks([_stock("72030", "トヨタ", "3700")])
    repo.upsert_financials(
        [
            _fin("72030", "2025-05-08", "FY", eps=359.56, bps=2753.09, dividend_per_share=90.0),
            # 最新は四半期: bps 空・配当は予想更新・株数あり
            _fin(
                "72030",
                "2026-02-06",
                "3Q",
                eps=232.55,
                bps=None,
                dividend_per_share=95.0,
                shares_outstanding=15_794_987_460.0,
                treasury_shares=2_761_600_733.0,
            ),
        ]
    )
    with get_engine().connect() as conn:
        latest = repo.get_latest_financials_by_code(conn)
        annual = repo.get_latest_annual_financials_by_code(conn)

    # 最新行（3Q）から配当・株数
    assert latest["72030"]["dividend_per_share"] == 95.0
    assert latest["72030"]["shares_outstanding"] == 15_794_987_460.0
    # 最新FY行から実績 eps/bps（四半期の累計 eps を拾わない）
    assert annual["72030"]["eps"] == 359.56
    assert annual["72030"]["bps"] == 2753.09


def _snap(code: str, per, pbr, mcap, dy) -> dict:
    return {
        "code": code,
        "as_of_date": "2026-06-03",
        "close": 1000.0,
        "eps": 100.0,
        "bps": 500.0,
        "dividend_per_share": 30.0,
        "shares_net": mcap / 1000.0 if mcap else None,
        "per": per,
        "pbr": pbr,
        "market_cap": mcap,
        "dividend_yield": dy,
        "fin_disclosed_date": "2025-05-08",
        "updated_at": "2026-06-04T00:00:00+00:00",
    }


def _seed_snapshots(temp_db) -> None:
    repo.upsert_stocks(
        [
            _stock("1000", "安いA", "3700"),  # 同業種
            _stock("1001", "高いB", "3700"),  # 同業種
            _stock("2000", "別業種C", "5250"),
            _stock("9999", "ETF", "3700", is_etf=1),
        ]
    )
    repo.upsert_valuation_snapshots(
        [
            _snap("1000", per=8.0, pbr=0.8, mcap=500e8, dy=0.04),
            _snap("1001", per=25.0, pbr=3.0, mcap=2000e8, dy=0.01),
            _snap("2000", per=12.0, pbr=1.5, mcap=8000e8, dy=0.02),
            _snap("9999", per=15.0, pbr=1.0, mcap=300e8, dy=0.0),
        ]
    )


def test_screen_absolute_range(temp_db) -> None:
    _seed_snapshots(temp_db)
    with get_engine().connect() as conn:
        rows = repo.screen_stocks(conn, {"per_max": 13.0, "dividend_yield_min": 0.015})
    codes = {r["code"] for r in rows}
    assert codes == {"1000", "2000"}  # per<=13 かつ 利回り>=1.5%
    # JOIN 補完
    assert next(r for r in rows if r["code"] == "1000")["company_name"] == "安いA"


def test_screen_exclude_etf_and_sector(temp_db) -> None:
    _seed_snapshots(temp_db)
    with get_engine().connect() as conn:
        rows = repo.screen_stocks(conn, {"sector33_code": "3700", "exclude_etf": True})
    assert {r["code"] for r in rows} == {"1000", "1001"}  # ETF 9999 を除外


def test_screen_sector_pctile_and_market_cap_rank(temp_db) -> None:
    _seed_snapshots(temp_db)
    with get_engine().connect() as conn:
        # 業種3700内で PER 最安（pctile<=0）→ 1000 のみ（1001 は高い）
        cheap = repo.screen_stocks(conn, {"sector33_code": "3700", "per_sector_pctile_max": 0.0})
        # 時価総額 上位 2 社
        top2 = repo.screen_stocks(conn, {"market_cap_rank_max": 2})
    assert cheap[0]["code"] == "1000"  # 業種内最安
    assert {r["code"] for r in top2} == {"2000", "1001"}  # 8000e8, 2000e8


def test_screen_sort_and_limit(temp_db) -> None:
    _seed_snapshots(temp_db)
    with get_engine().connect() as conn:
        rows = repo.screen_stocks(conn, {"sort_by": "per", "sort_dir": "asc", "limit": 2})
    assert [r["code"] for r in rows] == ["1000", "2000"]  # per 昇順 8,12


def test_screen_by_keyword(temp_db) -> None:
    """q で銘柄名・コードの部分一致検索（list_stocks と同じ LIKE OR）。"""
    _seed_snapshots(temp_db)
    with get_engine().connect() as conn:
        by_name = repo.screen_stocks(conn, {"q": "安い"})  # company_name 部分一致
        by_code = repo.screen_stocks(conn, {"q": "2000"})  # code 部分一致
    assert {r["code"] for r in by_name} == {"1000"}
    assert {r["code"] for r in by_code} == {"2000"}


def test_screening_filters_crud(temp_db) -> None:
    fid = repo.insert_screening_filter("割安高配当", '{"per_max":15,"dividend_yield_min":0.03}')
    with get_engine().connect() as conn:
        got = repo.get_screening_filter(conn, fid)
        all_f = repo.list_screening_filters(conn)
    assert got["name"] == "割安高配当"
    assert len(all_f) == 1

    n = repo.update_screening_filter(fid, "改名", '{"per_max":10}')
    assert n == 1
    with get_engine().connect() as conn:
        got2 = repo.get_screening_filter(conn, fid)
    assert got2["name"] == "改名"
    assert got2["criteria_json"] == '{"per_max":10}'

    d = repo.delete_screening_filter(fid)
    assert d == 1
    with get_engine().connect() as conn:
        assert repo.list_screening_filters(conn) == []
