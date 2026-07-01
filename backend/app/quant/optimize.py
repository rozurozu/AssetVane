"""平均分散最適化（PyPortfolioOpt）— policy 制約の写像。

設計の真実: docs/phase-specs/phase2-spec.md §4.3・§5（P2-6 ツール返却スキーマ）。

- **純関数・DB 非依存**（ADR-016）。入力 DataFrame/dict → 出力 dict。
- **AI に数値を計算させない**（ADR-014）。Python が最適比率を計算する。
- 期待リターン: `mean_historical_return`（年率 historical mean）（確定・L-14）。
- 共分散: `CovarianceShrinkage().ledoit_wolf()`（標本共分散は不安定＝確定・L-14）。
- 現金枠: 株式内で最適化し、最後に `(1 - cash_weight)` でスケールして返す方式。
  sector_caps は株式内ウェイト基準（株式の中の業種比率）で課す（注記: スケール後の
  実際のポートフォリオ比率は cap × (1-cash_weight) になる。ユーザーが望む「全資産内の
  業種上限」が必要な場合は呼び出し側で換算すること）。
- infeasible: 最適化例外 → infeasible=True・空 weights（422 ではなく 200 で返す）。
- パラメータは名前付きモジュール定数（magic number 禁止＝ADR-027）。
"""

from __future__ import annotations

from typing import Any, cast

import pandas as pd
from pypfopt import EfficientFrontier, expected_returns, risk_models

from app.quant._frame import column_has_nulls
from app.quant.portfolio import RISK_FREE_RATE

# 年率換算の営業日数（spec §4.1）
_TRADING_DAYS = 252

# ルックバック窓（共分散・リターン推定に使う日数）
_LOOKBACK = 252

# デフォルトのウェイト境界（no_leverage/max_position_weight が無い場合）
_DEFAULT_WEIGHT_LOWER = 0.0
_DEFAULT_WEIGHT_UPPER = 1.0


def optimize_portfolio(
    price_panel: pd.DataFrame,
    policy: dict[str, Any],
    sectors: dict[str, str],
    objective: str = "max_sharpe",
    current_weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    """候補銘柄群の price_panel から policy 制約付き平均分散最適化を行う。

    price_panel: index=date, columns=code, 値=adj_close（候補銘柄群）。
    policy: policy テーブルの構造化コア（spec §4.3 表参照）。
    sectors: code -> sector33_code（sector_caps 制約用）。
    objective: 'max_sharpe' | 'min_volatility' | 'efficient_return'。
    current_weights: 現在の構成比（0..1）。delta 計算用・任意。

    返却 dict は spec §5 P2-6 と同形（is_delayed は常に True）:
    {
        "as_of": <price_panel 最終 date 文字列 or None>,
        "is_delayed": True,
        "objective": str,
        "cash_weight": float,
        "weights": [{"code", "current_weight", "target_weight", "delta"}, ...],
        "expected_annual_return": float | None,
        "expected_annual_volatility": float | None,
        "expected_sharpe": float | None,
        "constraints_applied": {"target_cash_ratio", "max_position_weight", "sector_caps"},
        "infeasible": bool,
    }

    policy → 最適化制約の写像（ADR-013 二重活用・spec §4.3）:
    - exclusions: price_panel から列を除外。
    - max_position_weight: weight_bounds=(0, max_position_weight)。
    - target_cash_ratio: 株式合計 = 1 - target_cash_ratio とし、最後にスケール。
    - sector_caps: 株式内 EfficientFrontier.add_sector_constraints で課す。
    - no_leverage=1: long-only（下限 0）。
    （ADR-016: 純関数・DB 非依存・docs/phase-specs/phase2-spec.md §4.3）
    """
    # --- as_of ---
    as_of: str | None = None
    if price_panel is not None and not price_panel.empty:
        as_of = str(price_panel.index[-1])

    def _infeasible_result(obj: str, cash_w: float, constraints: dict[str, Any]) -> dict[str, Any]:
        return {
            "as_of": as_of,
            "is_delayed": True,
            "objective": obj,
            "cash_weight": cash_w,
            "weights": [],
            "expected_annual_return": None,
            "expected_annual_volatility": None,
            "expected_sharpe": None,
            "constraints_applied": constraints,
            "infeasible": True,
        }

    # --- policy から制約パラメータを読む ---
    exclusions: list[str] = list(policy.get("exclusions") or [])
    max_pos: float | None = policy.get("max_position_weight")
    cash_ratio: float = float(policy.get("target_cash_ratio") or 0.0)
    sector_caps: dict[str, float] = dict(policy.get("sector_caps") or {})
    no_leverage: int = int(policy.get("no_leverage") or 1)  # 既定は long-only
    target_return: float | None = policy.get("target_return")

    constraints_applied: dict[str, Any] = {
        "target_cash_ratio": cash_ratio if cash_ratio > 0.0 else None,
        "max_position_weight": max_pos,
        "sector_caps": sector_caps if sector_caps else None,
    }

    # --- exclusions を price_panel から除外 ---
    panel = price_panel.copy()
    if exclusions:
        drop_cols = [c for c in exclusions if c in panel.columns]
        panel = panel.drop(columns=drop_cols)

    if panel.empty or panel.shape[1] == 0:
        return _infeasible_result(objective, cash_ratio, constraints_applied)

    # --- ルックバック切り出し ---
    if len(panel) > _LOOKBACK:
        panel = panel.iloc[-_LOOKBACK:]

    # --- null 銘柄を除外（裁定 L-26）---
    valid_cols = [str(c) for c in panel.columns if not column_has_nulls(panel, str(c))]
    if not valid_cols:
        return _infeasible_result(objective, cash_ratio, constraints_applied)
    panel = panel[valid_cols]

    codes = list(panel.columns)

    # --- PyPortfolioOpt で期待リターン・共分散推定 ---
    try:
        mu = expected_returns.mean_historical_return(panel, frequency=_TRADING_DAYS)
        cov = risk_models.CovarianceShrinkage(panel, frequency=_TRADING_DAYS).ledoit_wolf()
    except Exception:
        return _infeasible_result(objective, cash_ratio, constraints_applied)

    # --- ウェイト境界（no_leverage=1 → long-only・下限 0）---
    lower = _DEFAULT_WEIGHT_LOWER if no_leverage else -1.0
    upper = float(max_pos) if max_pos is not None else _DEFAULT_WEIGHT_UPPER
    weight_bounds = (lower, upper)

    # --- EfficientFrontier を構築 ---
    try:
        ef = EfficientFrontier(mu, cov, weight_bounds=weight_bounds)

        # sector_caps 制約（株式内ウェイト基準）
        if sector_caps and sectors:
            sector_mapper = {c: sectors.get(c, "__unknown__") for c in codes}
            sector_upper: dict[str, float] = {}
            for code in codes:
                sec = sector_mapper.get(code, "__unknown__")
                if sec in sector_caps:
                    sector_upper[sec] = sector_caps[sec]
            if sector_upper:
                ef.add_sector_constraints(
                    sector_mapper=sector_mapper,
                    sector_lower={},
                    sector_upper=sector_upper,
                )

        # objective に応じて最適化を実行
        actual_objective = objective
        if objective == "max_sharpe":
            ef.max_sharpe(risk_free_rate=RISK_FREE_RATE)
        elif objective == "min_volatility":
            ef.min_volatility()
        elif objective == "efficient_return":
            if target_return is not None:
                ef.efficient_return(target_return=float(target_return))
            else:
                # target_return が無ければ max_sharpe にフォールバック
                actual_objective = "max_sharpe"
                ef.max_sharpe(risk_free_rate=RISK_FREE_RATE)
        else:
            # 不明な objective は max_sharpe にフォールバック
            actual_objective = "max_sharpe"
            ef.max_sharpe(risk_free_rate=RISK_FREE_RATE)

        # 株式内ウェイト（sum=1 基準）
        raw_weights = {str(code): float(weight) for code, weight in ef.clean_weights().items()}

        # portfolio_performance は clean_weights 呼び出し後に取得する
        exp_ret, exp_vol, exp_sharpe = ef.portfolio_performance(risk_free_rate=RISK_FREE_RATE)
    except Exception:
        return _infeasible_result(objective, cash_ratio, constraints_applied)

    # --- 現金枠スケール ---
    # 株式合計 = 1 - cash_ratio になるよう株式内ウェイトをスケール。
    stock_total = 1.0 - cash_ratio
    scaled_weights: dict[str, float] = {c: float(w) * stock_total for c, w in raw_weights.items()}

    # --- weights 配列を構築（spec §5 P2-6 形式）---
    weights_list: list[dict[str, Any]] = []
    for code in codes:
        target_w = scaled_weights.get(code, 0.0)
        current_w: float | None = (current_weights or {}).get(code)
        if current_w is None:
            delta: float = target_w  # current 無しなら delta = target（spec 指定）
        else:
            delta = target_w - float(current_w)
        weights_list.append(
            {
                "code": code,
                "current_weight": float(current_w) if current_w is not None else None,
                "target_weight": float(target_w),
                "delta": float(delta),
            }
        )

    return {
        "as_of": as_of,
        "is_delayed": True,
        "objective": actual_objective,
        "cash_weight": float(cash_ratio),
        "weights": weights_list,
        "expected_annual_return": float(cast(float, exp_ret)),
        "expected_annual_volatility": float(cast(float, exp_vol)),
        "expected_sharpe": float(cast(float, exp_sharpe)),
        "constraints_applied": constraints_applied,
        "infeasible": False,
    }
