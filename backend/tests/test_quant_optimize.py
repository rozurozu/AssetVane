"""quant.optimize の既知系列テスト（ADR-016: テスト済みコードで実装）。

設計の真実: docs/phase-specs/phase2-spec.md §4.3・§8。
DB に触れず手組み DataFrame で純関数を検証する（実 API も叩かない）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.quant.optimize import optimize_portfolio

# ---------------------------------------------------------------------------
# ヘルパ
# ---------------------------------------------------------------------------


def _rand_panel(
    n: int,
    codes: list[str],
    seed: int = 0,
) -> pd.DataFrame:
    """ランダムウォーク adj_close パネルを作る（再現性のために seed 固定）。

    seed=0・loc=0.001 で全銘柄の期待リターンが正になることを確認済み（max_sharpe 制約）。
    """
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2022-01-01", periods=n, freq="B")
    data: dict[str, list[float]] = {}
    for i, code in enumerate(codes):
        # 銘柄ごとに微妙にリターンが異なるランダムウォーク（正の期待リターン保証）
        r = rng.normal(loc=0.001 + i * 0.0001, scale=0.012, size=n)
        data[code] = list(np.cumprod(1.0 + r) * 100.0)
    return pd.DataFrame(data, index=dates)


def _base_policy(**kwargs: object) -> dict:
    """最低限の policy（no_leverage=1 のみ）に kwargs を上書きする。"""
    base: dict = {"no_leverage": 1}
    base.update(kwargs)
    return base


# ---------------------------------------------------------------------------
# long-only: 全 w>=0 かつ sum(w)+cash_weight≒1
# ---------------------------------------------------------------------------


def test_long_only_weights_sum_to_one() -> None:
    """no_leverage=1 で全 target_weight>=0 かつ sum + cash_weight ≒ 1.0。"""
    panel = _rand_panel(300, ["A", "B", "C", "D"])
    policy = _base_policy(no_leverage=1)
    result = optimize_portfolio(panel, policy, sectors={})

    assert result["infeasible"] is False
    weights = result["weights"]
    assert all(w["target_weight"] >= -1e-9 for w in weights)
    total = sum(w["target_weight"] for w in weights) + result["cash_weight"]
    assert abs(total - 1.0) < 1e-6


# ---------------------------------------------------------------------------
# max_position_weight: 全 target_weight <= limit + ε
# ---------------------------------------------------------------------------


def test_max_position_weight_respected() -> None:
    """max_position_weight=0.2 で全 target_weight <= 0.20 + ε。"""
    panel = _rand_panel(300, ["A", "B", "C", "D", "E"])
    policy = _base_policy(max_position_weight=0.2)
    result = optimize_portfolio(panel, policy, sectors={})

    assert result["infeasible"] is False
    for w in result["weights"]:
        assert w["target_weight"] <= 0.20 + 1e-6, (
            f"{w['code']} target_weight={w['target_weight']:.4f} > 0.20"
        )


# ---------------------------------------------------------------------------
# sector_caps: 同業種合計が cap 以下
# ---------------------------------------------------------------------------


def test_sector_caps_respected() -> None:
    """sector_caps={'電気機器': 0.30} で電気機器銘柄の合計ウェイトが 0.30 以下。"""
    codes = ["A", "B", "C", "D"]
    panel = _rand_panel(300, codes)
    sectors = {"A": "電気機器", "B": "電気機器", "C": "輸送用機器", "D": "輸送用機器"}
    policy = _base_policy(sector_caps={"電気機器": 0.30})
    result = optimize_portfolio(panel, policy, sectors=sectors)

    assert result["infeasible"] is False
    elec_total = sum(w["target_weight"] for w in result["weights"] if w["code"] in ("A", "B"))
    assert elec_total <= 0.30 + 1e-6, f"電気機器合計={elec_total:.4f} > 0.30"


# ---------------------------------------------------------------------------
# infeasible: 矛盾制約で infeasible=True・空 weights
# ---------------------------------------------------------------------------


def test_infeasible_on_contradictory_constraints() -> None:
    """max_position_weight=0.1 かつ銘柄2つ・cash 0 → sum 最大 0.2 で sum=1 に届かず infeasible。

    PyPortfolioOpt は sum(w)=1 をデフォルト制約とするため、
    max_position_weight=0.1 × 2 銘柄 = 最大 0.2 < 1.0 となり解なし。
    """
    # 2 銘柄のみ
    panel = _rand_panel(300, ["X", "Y"])
    # max_position_weight=0.1 → 各銘柄最大 0.1 → 合計最大 0.2（現金=0）
    # PyPortfolioOpt のデフォルト sum=1 制約と矛盾
    policy = _base_policy(max_position_weight=0.1, target_cash_ratio=0.0)
    result = optimize_portfolio(panel, policy, sectors={})

    assert result["infeasible"] is True
    assert result["weights"] == []


# ---------------------------------------------------------------------------
# current_weights → delta の計算
# ---------------------------------------------------------------------------


def test_delta_computed_when_current_weights_given() -> None:
    """current_weights を渡した場合、delta = target - current になる。"""
    panel = _rand_panel(300, ["A", "B", "C"])
    policy = _base_policy()
    current = {"A": 0.5, "B": 0.3, "C": 0.2}
    result = optimize_portfolio(panel, policy, sectors={}, current_weights=current)

    assert result["infeasible"] is False
    for w in result["weights"]:
        code = w["code"]
        if w["current_weight"] is not None:
            expected_delta = w["target_weight"] - w["current_weight"]
            assert abs(w["delta"] - expected_delta) < 1e-9, (
                f"{code}: delta={w['delta']:.6f} != target-current={expected_delta:.6f}"
            )


# ---------------------------------------------------------------------------
# target_cash_ratio: stock 合計 = (1 - cash_ratio)
# ---------------------------------------------------------------------------


def test_target_cash_ratio_applied() -> None:
    """target_cash_ratio=0.20 → 株式合計ウェイトが 0.80 ± ε。"""
    panel = _rand_panel(300, ["A", "B", "C", "D"])
    policy = _base_policy(target_cash_ratio=0.20)
    result = optimize_portfolio(panel, policy, sectors={})

    assert result["infeasible"] is False
    assert abs(result["cash_weight"] - 0.20) < 1e-9
    stock_total = sum(w["target_weight"] for w in result["weights"])
    assert abs(stock_total - 0.80) < 1e-6


# ---------------------------------------------------------------------------
# exclusions: 対象銘柄が結果に含まれない
# ---------------------------------------------------------------------------


def test_exclusions_applied() -> None:
    """exclusions に含めた銘柄は weights に出てこない。"""
    panel = _rand_panel(300, ["A", "B", "C", "D"])
    policy = _base_policy(exclusions=["D"])
    result = optimize_portfolio(panel, policy, sectors={})

    assert result["infeasible"] is False
    codes_in_result = {w["code"] for w in result["weights"]}
    assert "D" not in codes_in_result


# ---------------------------------------------------------------------------
# objective='min_volatility'
# ---------------------------------------------------------------------------


def test_min_volatility_objective() -> None:
    """objective='min_volatility' で正常に返り infeasible=False。"""
    panel = _rand_panel(300, ["A", "B", "C"])
    policy = _base_policy()
    result = optimize_portfolio(panel, policy, sectors={}, objective="min_volatility")
    assert result["infeasible"] is False
    assert result["objective"] == "min_volatility"


# ---------------------------------------------------------------------------
# is_delayed は常に True（ADR-008）
# ---------------------------------------------------------------------------


def test_is_delayed_always_true() -> None:
    """is_delayed は Free 12週遅延のため常に True（ADR-008）。"""
    panel = _rand_panel(300, ["A", "B"])
    result = optimize_portfolio(panel, _base_policy(), sectors={})
    assert result["is_delayed"] is True


# ---------------------------------------------------------------------------
# constraints_applied に使用制約が反映される
# ---------------------------------------------------------------------------


def test_constraints_applied_field() -> None:
    """constraints_applied に policy から使った制約が入る。"""
    panel = _rand_panel(300, ["A", "B", "C"])
    policy = _base_policy(max_position_weight=0.4, target_cash_ratio=0.10)
    result = optimize_portfolio(panel, policy, sectors={})
    c = result["constraints_applied"]
    assert abs(c["target_cash_ratio"] - 0.10) < 1e-9
    assert abs(c["max_position_weight"] - 0.40) < 1e-9
