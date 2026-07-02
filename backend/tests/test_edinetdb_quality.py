"""services.edinetdb_quality（#2 売掛/在庫の質の組み立て）の純粋テスト（ADR-064）。

DB に触れず正規化済み行を直書きで検証する（testing-strategy）。DSO/DIO・YoY の採用規律
（当期=revenue 最大 fiscal_year・YoY=前年・COGS=cost_of_sales 優先→revenue−gross_profit）を固定。
"""

from __future__ import annotations

from app.services.edinetdb_quality import compute_quality_from_financials


def _row(fy, recv=None, inv=None, rev=None, gp=None, cogs=None, disclosed=None):
    return {
        "fiscal_year": fy,
        "disclosed_date": disclosed,
        "receivables": recv,
        "inventory": inv,
        "revenue": rev,
        "gross_profit": gp,
        "cost_of_sales": cogs,
    }


def test_normal_dso_dio_and_yoy() -> None:
    rows = [
        _row(2024, recv=100.0, inv=200.0, rev=1000.0, gp=300.0, disclosed="2024-06-18"),
        _row(2025, recv=150.0, inv=260.0, rev=1100.0, gp=330.0, disclosed="2025-06-18"),
    ]
    q = compute_quality_from_financials(rows)
    assert q is not None
    # DSO = 150/1100×365、DIO = 260/(1100-330)×365
    assert abs(q["receivables_turnover_days"] - 150.0 / 1100.0 * 365.0) < 1e-9
    assert abs(q["inventory_turnover_days"] - 260.0 / (1100.0 - 330.0) * 365.0) < 1e-9
    # 受取債権 50% 増 vs 売上 10% 増＝乖離シグナル
    assert abs(q["receivables_growth_yoy"] - 0.5) < 1e-12
    assert abs(q["inventory_growth_yoy"] - 0.3) < 1e-12
    assert q["fin_disclosed_date"] == "2025-06-18"


def test_cost_of_sales_direct_preferred_over_gross_profit() -> None:
    # cost_of_sales が直接あれば revenue−gross_profit より優先
    rows = [_row(2025, recv=100.0, inv=300.0, rev=1000.0, gp=400.0, cogs=600.0)]
    q = compute_quality_from_financials(rows)
    assert q is not None
    assert abs(q["inventory_turnover_days"] - 300.0 / 600.0 * 365.0) < 1e-9


def test_dio_none_when_no_cogs_derivable() -> None:
    # gross_profit も cost_of_sales も無ければ DIO は None（捏造しない）。DSO は出る。
    rows = [_row(2025, recv=100.0, inv=300.0, rev=1000.0)]
    q = compute_quality_from_financials(rows)
    assert q is not None
    assert q["inventory_turnover_days"] is None
    assert q["receivables_turnover_days"] is not None


def test_yoy_none_without_prev_year() -> None:
    # 前年（fiscal_year-1）行が無ければ YoY は None（>1年差は誤解を招く）
    rows = [
        _row(2022, recv=80.0, inv=100.0, rev=900.0, gp=270.0),  # 3 年前（gap）
        _row(2025, recv=150.0, inv=260.0, rev=1100.0, gp=330.0),
    ]
    q = compute_quality_from_financials(rows)
    assert q is not None
    assert q["receivables_growth_yoy"] is None
    assert q["inventory_growth_yoy"] is None
    # 当期の DSO/DIO は出る
    assert q["receivables_turnover_days"] is not None


def test_none_when_no_usable_current_row() -> None:
    assert compute_quality_from_financials([]) is None
    # revenue が無い行だけ → None
    assert compute_quality_from_financials([_row(2025, recv=100.0, inv=200.0)]) is None
    # revenue はあるが受取債権も在庫も無い（サマリのみ）→ None
    assert compute_quality_from_financials([_row(2025, rev=1000.0)]) is None
