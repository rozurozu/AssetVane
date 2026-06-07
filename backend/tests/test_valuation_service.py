"""services.valuation の組み立て検証（ADR-031）。一時 SQLite・ネットに出ない。"""

from __future__ import annotations

from app.db import repo
from app.db.engine import get_engine
from app.services import valuation as valsvc


def _stock(code: str, sector: str = "3700", is_etf: int = 0) -> dict:
    return {
        "code": code,
        "company_name": f"会社{code}",
        "sector33_code": sector,
        "sector17_code": "6",
        "market_code": "0111",
        "is_etf": is_etf,
        "updated_at": "2026-06-04T00:00:00+00:00",
    }


def _quote(code: str, date: str, close: float) -> dict:
    return {
        "code": code,
        "date": date,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1.0,
        "adj_close": close,
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


def test_build_snapshot_uses_fy_eps_bps_and_latest_dividend_shares(temp_db) -> None:
    repo.upsert_stocks([_stock("72030")])
    # 最新営業日は 2026-06-03（古い日も入れて MAX 採用を確認）
    repo.upsert_daily_quotes(
        [_quote("72030", "2026-05-01", 2000.0), _quote("72030", "2026-06-03", 2500.0)]
    )
    repo.upsert_financials(
        [
            _fin("72030", "2025-05-08", "FY", eps=359.56, bps=2753.09, dividend_per_share=90.0),
            _fin(
                "72030",
                "2026-02-06",
                "3Q",
                eps=232.55,  # 累計 EPS（採用しない）
                bps=None,
                dividend_per_share=95.0,
                shares_outstanding=15_794_987_460.0,
                treasury_shares=2_761_600_733.0,
            ),
        ]
    )
    with get_engine().connect() as conn:
        rows = valsvc.build_valuation_snapshots(conn)

    assert len(rows) == 1
    r = rows[0]
    assert r["as_of_date"] == "2026-06-03"
    assert r["close"] == 2500.0
    # FY 実績で PER/PBR（累計 232.55 ではなく 359.56 を使う）
    assert abs(r["per"] - 2500.0 / 359.56) < 1e-9
    assert abs(r["pbr"] - 2500.0 / 2753.09) < 1e-9
    # 最新行の予想配当で利回り
    assert abs(r["dividend_yield"] - 95.0 / 2500.0) < 1e-9
    # 時価総額 = close * (発行済 - 自己株)
    shares_net = 15_794_987_460.0 - 2_761_600_733.0
    assert r["shares_net"] == shares_net
    assert abs(r["market_cap"] - 2500.0 * shares_net) < 1.0


def test_build_snapshot_computes_roe_margins_and_yoy_growth(temp_db) -> None:
    # 当期FY＋前期FY を入れ、ROE/利益率/YoY 成長率が当期FY・前期FY から組まれる（ADR-048）
    repo.upsert_stocks([_stock("90000")])
    repo.upsert_daily_quotes([_quote("90000", "2026-06-03", 1000.0)])
    repo.upsert_financials(
        [
            _fin(
                "90000",
                "2024-05-10",
                "FY",  # 前期FY（YoY 前年同期）
                net_sales=1000.0,
                operating_profit=80.0,
                profit=50.0,
                eps=50.0,
                bps=500.0,
            ),
            _fin(
                "90000",
                "2025-05-10",
                "FY",  # 当期FY（採用・利益率/ROE/YoY の当期）
                net_sales=1200.0,
                operating_profit=120.0,
                profit=72.0,
                eps=60.0,
                bps=600.0,
            ),
        ]
    )
    with get_engine().connect() as conn:
        rows = valsvc.build_valuation_snapshots(conn)

    r = next(x for x in rows if x["code"] == "90000")
    # ROE = 当期FY eps/bps = 60/600
    assert abs(r["roe"] - 60.0 / 600.0) < 1e-12
    # 利益率 = 当期FY op/sales, profit/sales
    assert abs(r["operating_margin"] - 120.0 / 1200.0) < 1e-12
    assert abs(r["net_margin"] - 72.0 / 1200.0) < 1e-12
    # YoY = 当期FY / 前期FY - 1
    assert abs(r["revenue_growth_yoy"] - (1200.0 / 1000.0 - 1)) < 1e-12
    assert abs(r["op_growth_yoy"] - (120.0 / 80.0 - 1)) < 1e-12
    assert abs(r["profit_growth_yoy"] - (72.0 / 50.0 - 1)) < 1e-12
    assert abs(r["eps_growth_yoy"] - (60.0 / 50.0 - 1)) < 1e-12


def test_build_snapshot_yoy_none_without_prior_fy(temp_db) -> None:
    # 前期FY が無い（新規上場等）→ 成長率は None・ROE/利益率は当期FYから出る（ADR-048）
    repo.upsert_stocks([_stock("90001")])
    repo.upsert_daily_quotes([_quote("90001", "2026-06-03", 1000.0)])
    repo.upsert_financials(
        [
            _fin(
                "90001",
                "2025-05-10",
                "FY",
                net_sales=1200.0,
                operating_profit=120.0,
                profit=72.0,
                eps=60.0,
                bps=600.0,
            )
        ]
    )
    with get_engine().connect() as conn:
        rows = valsvc.build_valuation_snapshots(conn)
    r = next(x for x in rows if x["code"] == "90001")
    assert abs(r["roe"] - 0.1) < 1e-12
    assert r["revenue_growth_yoy"] is None
    assert r["eps_growth_yoy"] is None


def test_build_snapshot_priced_but_no_financials_gives_none_metrics(temp_db) -> None:
    # 財務の無い銘柄（ETF 等）も価格があれば行は作る・各指標 None
    repo.upsert_stocks([_stock("13060", is_etf=1)])
    repo.upsert_daily_quotes([_quote("13060", "2026-06-03", 3000.0)])
    with get_engine().connect() as conn:
        rows = valsvc.build_valuation_snapshots(conn)
    assert len(rows) == 1
    r = rows[0]
    assert r["close"] == 3000.0
    assert r["per"] is None and r["pbr"] is None
    assert r["market_cap"] is None and r["dividend_yield"] is None
