"""quant.compute_risk_contributions の既知系列テスト（#4・ADR-085・ADR-016）。

DB に触れず手組み DataFrame で純関数を検証する。リスク寄与分解の要点:
- Σ component == portfolio の年率 vol（compute_portfolio_metrics と厳密整合）、Σ percent == 1。
- 完全相関・同一資産では percent == weight（既知値）。
- 境界（空・全 null・履歴不足・入力非破壊）で数値を捏造しない。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.quant.portfolio import compute_portfolio_metrics, compute_risk_contributions


def _panel(prices: dict[str, list[float]], base_date: str = "2024-01-01") -> pd.DataFrame:
    n = max(len(v) for v in prices.values())
    dates = pd.date_range(base_date, periods=n, freq="B")
    return pd.DataFrame(prices, index=dates)


def test_components_sum_to_annual_volatility() -> None:
    """Σ component ≈ annual_volatility、Σ percent ≈ 1、vol は compute_portfolio_metrics と一致。"""
    np.random.seed(7)
    n = 120
    p1 = np.cumprod(1.0 + np.random.normal(0.0005, 0.01, n)) * 100.0
    p2 = np.cumprod(1.0 + np.random.normal(0.0003, 0.012, n)) * 100.0
    p3 = np.cumprod(1.0 + np.random.normal(0.0001, 0.008, n)) * 100.0
    panel = _panel({"A": list(p1), "B": list(p2), "C": list(p3)})
    weights = {"A": 0.5, "B": 0.3, "C": 0.2}

    risk = compute_risk_contributions(panel, weights)
    metrics = compute_portfolio_metrics(panel, weights)

    assert risk["annual_volatility"] is not None
    # σ_p は portfolio 年率 vol と厳密一致（同じ標本ベース）。
    assert abs(risk["annual_volatility"] - metrics["annual_volatility"]) < 1e-9

    total_component = sum(c["component"] for c in risk["contributions"])
    total_percent = sum(c["percent"] for c in risk["contributions"])
    assert abs(total_component - risk["annual_volatility"]) < 1e-9
    assert abs(total_percent - 1.0) < 1e-9


def test_percent_equals_weight_for_identical_correlated_assets() -> None:
    """完全相関・同一 vol の 2 資産では percent == weight（既知値・単調性も担保）。

    同一系列 → Σ = [[σ²,σ²],[σ²,σ²]]。w1+w2=1 のとき CCR_i = w_i·σ ⇒ percent_i = w_i。
    """
    np.random.seed(3)
    series = list(np.cumprod(1.0 + np.random.normal(0.0004, 0.009, 100)) * 100.0)
    panel = _panel({"A": series, "B": list(series)})  # 完全に同一の価格系列
    risk = compute_risk_contributions(panel, {"A": 0.7, "B": 0.3})

    by_code = {c["code"]: c for c in risk["contributions"]}
    assert abs(by_code["A"]["percent"] - 0.7) < 1e-9
    assert abs(by_code["B"]["percent"] - 0.3) < 1e-9
    # 単調性: ウェイトが大きい A の寄与が B より大きい。
    assert by_code["A"]["percent"] > by_code["B"]["percent"]


def test_single_stock_percent_is_one() -> None:
    """1 銘柄なら percent=1.0・component は単一銘柄 vol と一致。"""
    series = list(np.linspace(100.0, 130.0, 60))
    panel = _panel({"X": series})
    risk = compute_risk_contributions(panel, {"X": 1.0})

    assert len(risk["contributions"]) == 1
    only = risk["contributions"][0]
    assert abs(only["percent"] - 1.0) < 1e-9
    assert abs(only["component"] - risk["annual_volatility"]) < 1e-9


def test_empty_panel_returns_none() -> None:
    risk = compute_risk_contributions(pd.DataFrame(), {"A": 1.0})
    assert risk["annual_volatility"] is None
    assert risk["contributions"] == []


def test_all_null_returns_none() -> None:
    panel = _panel({"A": [float("nan")] * 20})
    risk = compute_risk_contributions(panel, {"A": 1.0})
    assert risk["annual_volatility"] is None
    assert risk["contributions"] == []


def test_insufficient_history_returns_none() -> None:
    """履歴 < 2（日次リターン 1 本未満）→ 分解不能で空。"""
    panel = _panel({"A": [100.0, 101.0]})  # pct_change().dropna() は 1 行 → <2
    risk = compute_risk_contributions(panel, {"A": 1.0})
    assert risk["contributions"] == []


def test_input_panel_not_mutated() -> None:
    """入力 DataFrame は破壊しない（純関数・ADR-016）。"""
    panel = _panel({"A": list(np.linspace(100, 120, 30)), "B": list(np.linspace(50, 70, 30))})
    before = panel.copy()
    compute_risk_contributions(panel, {"A": 0.5, "B": 0.5})
    pd.testing.assert_frame_equal(panel, before)


def test_no_is_delayed_key() -> None:
    """quant は is_delayed を返さない（ADR-071）。as_of だけ返す。"""
    panel = _panel({"A": list(np.linspace(100, 120, 30)), "B": list(np.linspace(50, 70, 30))})
    risk = compute_risk_contributions(panel, {"A": 0.5, "B": 0.5})
    assert "is_delayed" not in risk
    assert "as_of" in risk
