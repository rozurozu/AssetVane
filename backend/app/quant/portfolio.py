"""ポートフォリオ指標・policy 逸脱の純関数。

設計の真実: docs/phase-specs/phase2-spec.md §4.1・§4.2・§5（P2-5 ツール返却スキーマ）。

- **純関数・DB 非依存**（ADR-016）。入力 DataFrame/dict → 出力 dict。
- **AI に数値を計算させない**（ADR-014）。Python がシャープ・最大DD・逸脱を計算し、
  LLM は Tool で受け取った事実を解釈・提案するだけ。
- adj_close 窓内 null の銘柄・日は skip（補間しない＝裁定 L-26）。
- パラメータは名前付きモジュール定数（magic number 禁止＝ADR-027）。
- 年率換算: 252 営業日（spec §4.1 表）。
- 比率・weight は 0..1（UI でのみ ×100 して %）。
"""

from __future__ import annotations

from math import sqrt
from typing import Any, cast

import pandas as pd

# 無リスク金利（U-3 裁定済み・ADR-027。env 不可・policy 不可・将来 method_settings へ）
RISK_FREE_RATE = 0.0

# 年率換算の営業日数（spec §4.1）
_TRADING_DAYS = 252

# ルックバック窓（直近何営業日を使うか）
_LOOKBACK = 252


def _column_has_nulls(frame: pd.DataFrame, column: str) -> bool:
    """Pandas の列取得が Series/DataFrame どちらでも null 有無を bool で返す。"""
    values = frame[column]
    if isinstance(values, pd.DataFrame):
        return bool(values.isna().to_numpy().any())
    series = cast(pd.Series, values)
    return bool(series.isna().any())


# ---------------------------------------------------------------------------
# compute_deviations
# ---------------------------------------------------------------------------


def compute_deviations(
    weights: dict[str, float],
    cash_ratio: float,
    sector_weights: dict[str, float],
    policy: dict[str, Any],
    labels: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """policy 逸脱を検出し [{kind, label, current, limit, breached}] で返す。

    kind は REST 表記（spec §5 P2-7 と一致）:
    - "max_position" … 1 銘柄が max_position_weight を超過（上回りで違反）。
    - "cash_ratio"   … 現金比率が target_cash_ratio を下回る（下回りで違反）。
    - "sector_cap"   … 業種合計ウェイトが sector_caps の上限を超過（上回りで違反）。

    current/limit は 0..1。
    （ADR-016: 純関数・DB 非依存・docs/phase-specs/phase2-spec.md §4.2）
    """
    deviations: list[dict[str, Any]] = []

    # --- position: 最大ウェイト銘柄 1 件 ---
    max_pos_limit: float | None = policy.get("max_position_weight")
    if max_pos_limit is not None and weights:
        # 最大ウェイトの銘柄を 1 件だけ出す
        max_code = max(weights, key=lambda c: weights[c])
        max_w = float(weights[max_code])
        label_pos = (labels or {}).get(max_code, max_code)
        deviations.append(
            {
                "kind": "max_position",
                "label": label_pos,
                "current": max_w,
                "limit": float(max_pos_limit),
                "breached": max_w > float(max_pos_limit),
            }
        )

    # --- cash: 下回りで違反 ---
    target_cash: float | None = policy.get("target_cash_ratio")
    if target_cash is not None:
        deviations.append(
            {
                "kind": "cash_ratio",
                "label": "現金比率",
                "current": float(cash_ratio),
                "limit": float(target_cash),
                "breached": float(cash_ratio) < float(target_cash),  # 下回りで違反
            }
        )

    # --- sector: 各業種の上限超過 ---
    sector_caps: dict[str, float] = policy.get("sector_caps") or {}
    for sector, cap in sector_caps.items():
        sw = float(sector_weights.get(sector, 0.0))
        deviations.append(
            {
                "kind": "sector_cap",
                "label": str(sector),
                "current": sw,
                "limit": float(cap),
                "breached": sw > float(cap),
            }
        )

    return deviations


# ---------------------------------------------------------------------------
# compute_portfolio_metrics
# ---------------------------------------------------------------------------


def compute_portfolio_metrics(
    price_panel: pd.DataFrame,
    weights: dict[str, float],
    policy: dict[str, Any] | None = None,
    labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    """保有銘柄の adj_close パネルからポートフォリオ指標・相関・逸脱を返す。

    price_panel: index=date, columns=code, 値=adj_close（保有銘柄ぶん）。
    weights: 現在の構成比（時価ベース・0..1）。
    policy: 逸脱判定に使う（None なら deviations は空配列）。
    labels: code -> company_name（correlation.labels 用）。

    返却 dict は spec §5 P2-5 と同形（is_delayed は常に True）:
    {
        "as_of": <price_panel 最終 date 文字列 or None>,
        "is_delayed": True,
        "annual_return": float | None,
        "annual_volatility": float | None,
        "sharpe": float | None,
        "max_drawdown": float | None,
        "lookback_days": int | None,
        "correlation": {"codes": [...], "labels": [...], "matrix": [[...]]},
        "deviations": [...],
    }

    adj_close 窓内 null の銘柄・日は skip（補間しない＝裁定 L-26）。
    1 銘柄以下・履歴不足の場合は指標を None、correlation を空にして返す。
    （ADR-016: 純関数・DB 非依存・docs/phase-specs/phase2-spec.md §4.1）
    """
    # --- as_of ---
    as_of: str | None = None
    if price_panel is not None and not price_panel.empty:
        last_idx = price_panel.index[-1]
        as_of = str(last_idx)

    # --- デフォルト返却値（計算不可時に使う）---
    def _empty_result(reason: str = "") -> dict[str, Any]:  # noqa: ANN001 (ローカル関数)
        return {
            "as_of": as_of,
            "is_delayed": True,
            "annual_return": None,
            "annual_volatility": None,
            "sharpe": None,
            "max_drawdown": None,
            "lookback_days": None,
            "correlation": {"codes": [], "labels": [], "matrix": []},
            "deviations": compute_deviations(weights, 0.0, {}, policy, labels)
            if policy is not None
            else [],
        }

    if price_panel is None or price_panel.empty:
        return _empty_result()

    # --- ルックバック切り出し ---
    panel = price_panel.copy()
    if len(panel) > _LOOKBACK:
        panel = panel.iloc[-_LOOKBACK:]

    # --- null 列（銘柄）を除外: 窓内に 1 つでも null があれば skip（裁定 L-26）---
    valid_cols = [str(c) for c in panel.columns if not _column_has_nulls(panel, str(c))]
    if not valid_cols:
        return _empty_result()
    panel = panel[valid_cols]

    # --- 日次リターン（pct_change で前日との差・NaN 行は dropna で除去）---
    ret = cast(pd.DataFrame, panel.pct_change().dropna())

    lookback_days = int(len(ret))
    if lookback_days < 2:
        return _empty_result()

    # --- 相関行列 ---
    codes = list(panel.columns)
    label_list = [(labels or {}).get(c, c) for c in codes]
    if len(codes) >= 2:
        corr_matrix = ret.corr()
        matrix: list[list[float]] = [[float(corr_matrix.loc[r, c]) for c in codes] for r in codes]
    else:
        # 1 銘柄 → 相関行列は空（定義できない）
        codes = []
        label_list = []
        matrix = []

    correlation: dict[str, Any] = {
        "codes": codes,
        "labels": label_list,
        "matrix": matrix,
    }

    # --- ポートフォリオ日次リターン ---
    # weights dict から valid_cols に合わせてウェイトベクトルを組む。
    # valid_cols にない銘柄は 0 として扱い、合計で再正規化する。
    w_raw = {c: float(weights.get(c, 0.0)) for c in valid_cols}
    w_sum = sum(w_raw.values())
    if w_sum <= 0.0:
        # ウェイトが全部 0 → 均等に割り当て
        w_vec = pd.Series({c: 1.0 / len(valid_cols) for c in valid_cols})
    else:
        w_vec = pd.Series({c: v / w_sum for c, v in w_raw.items()})

    port_ret: pd.Series = (ret * w_vec).sum(axis=1)

    # --- 指標計算（spec §4.1 表）---
    annual_return = float(port_ret.mean() * _TRADING_DAYS)
    annual_volatility = float(port_ret.std(ddof=1) * sqrt(_TRADING_DAYS))
    if annual_volatility > 0.0:
        sharpe = float((annual_return - RISK_FREE_RATE) / annual_volatility)
    else:
        sharpe = None

    # 最大ドローダウン（負値）
    cum = (1.0 + port_ret).cumprod()
    dd = cum / cum.cummax() - 1.0
    max_drawdown = float(dd.min())

    # --- deviations ---
    deviations = (
        compute_deviations(
            weights=weights,
            cash_ratio=0.0,  # cash_ratio は呼び出し側が持つ情報（本関数は weights のみ受け取る）
            sector_weights={},  # sector も同様（呼び出し側 app が完全版を供給する設計）
            policy=policy,
            labels=labels,
        )
        if policy is not None
        else []
    )

    return {
        "as_of": as_of,
        "is_delayed": True,
        "annual_return": annual_return,
        "annual_volatility": annual_volatility,
        "sharpe": sharpe,
        "max_drawdown": max_drawdown,
        "lookback_days": lookback_days,
        "correlation": correlation,
        "deviations": deviations,
    }
