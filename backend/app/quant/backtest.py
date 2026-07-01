"""ポートフォリオ・バックテスト（対指数・buy&hold）。

設計の真実: docs/phase-specs/phase2-spec.md §4.4・§8。

- **純関数・DB 非依存**（ADR-016）。入力 DataFrame/Series → 出力 dict。
- **AI に数値を計算させない**（ADR-014）。Python が累積リターン・シャープ・最大DD を計算。
- 取引コスト・手数料・スリッページは無視（提示用途・確定 L-15）。
  実弾運用時は別途手数料分を考慮すること。
- rebalance='monthly' は引数のみ予約（Phase 2 初期は 'none'=buy&hold と同じ挙動）。
- 年率換算: 252 営業日（spec §4.1）。
- パラメータは名前付きモジュール定数（magic number 禁止＝ADR-027）。
"""

from __future__ import annotations

from math import sqrt
from typing import Any, cast

import pandas as pd

from app.quant._frame import column_has_nulls
from app.quant.portfolio import RISK_FREE_RATE

# 年率換算の営業日数（spec §4.1）
_TRADING_DAYS = 252


def _compute_curve_stats(
    daily_ret: pd.Series,
) -> dict[str, Any]:
    """日次リターン系列から統計と累積曲線を計算して返す（内部ヘルパ）。

    返却:
    {
        "cumulative_return": float,
        "annual_return": float,
        "sharpe": float | None,
        "max_drawdown": float,
        "curve": [{"date": str, "value": float}, ...],
    }
    """
    if len(daily_ret) == 0:
        return {
            "cumulative_return": 0.0,
            "annual_return": 0.0,
            "sharpe": None,
            "max_drawdown": 0.0,
            "curve": [],
        }

    # 累積リターン曲線（1 始まり）
    cum: pd.Series = (1.0 + daily_ret).cumprod()

    # 最大ドローダウン（負値）
    dd = cum / cum.cummax() - 1.0
    max_dd = float(dd.min())

    # 累積リターン（終端 - 1）
    cumulative_return = float(cum.iloc[-1] - 1.0)

    # 年率リターン・ボラ・シャープ
    ann_ret = float(daily_ret.mean() * _TRADING_DAYS)
    ann_vol = float(daily_ret.std(ddof=1) * sqrt(_TRADING_DAYS))
    sharpe: float | None = float((ann_ret - RISK_FREE_RATE) / ann_vol) if ann_vol > 0.0 else None

    # 描画用曲線（{date, value} の配列。date は index の文字列化）
    curve = [
        {"date": str(idx), "value": float(val)}
        for idx, val in zip(cum.index, cum.values, strict=True)
    ]

    return {
        "cumulative_return": cumulative_return,
        "annual_return": ann_ret,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "curve": curve,
    }


def backtest_portfolio(
    price_panel: pd.DataFrame,
    weights: dict[str, float],
    benchmark: pd.Series,
    rebalance: str = "none",
) -> dict[str, Any]:
    """固定ウェイトの buy&hold バックテスト（対指数比較）。

    price_panel: index=date, columns=code, 値=adj_close（候補/保有銘柄）。
    weights: 検証する固定ウェイト（最適化結果等・0..1）。
    benchmark: 主要指数の水準 Series（index=date, 値=close）。
    rebalance: 'none'=buy&hold（初期実装）| 'monthly'（予約・Phase 2 初期は none と同じ）。
               ※ 月次リバランスは引数のみ予約。現状は 'monthly' を渡しても 'none' と同じ挙動。

    ※ 取引コスト・手数料・スリッページは無視（確定 L-15・提示用途）。
    ※ is_delayed は返さない。鮮度は呼び出し側が as_of から判定する
      （ADR-071・quant は today を知らない純関数）。

    返却 dict:
    {
        "as_of": str | None,
        "portfolio": {
            "cumulative_return": float,
            "annual_return": float,
            "sharpe": float | None,
            "max_drawdown": float,
            "curve": [{"date": str, "value": float}, ...],
        },
        "benchmark": { ...(同形)... },
        "excess_return": float,
    }
    （ADR-016: 純関数・DB 非依存・docs/phase-specs/phase2-spec.md §4.4）
    """
    # --- as_of ---
    as_of: str | None = None
    if price_panel is not None and not price_panel.empty:
        as_of = str(price_panel.index[-1])

    def _empty_result() -> dict[str, Any]:
        empty_stats: dict[str, Any] = {
            "cumulative_return": 0.0,
            "annual_return": 0.0,
            "sharpe": None,
            "max_drawdown": 0.0,
            "curve": [],
        }
        return {
            "as_of": as_of,
            "portfolio": empty_stats,
            "benchmark": dict(empty_stats),
            "excess_return": 0.0,
        }

    if price_panel is None or price_panel.empty:
        return _empty_result()

    # --- null 銘柄を除外（裁定 L-26）---
    valid_cols = [str(c) for c in price_panel.columns if not column_has_nulls(price_panel, str(c))]
    if not valid_cols:
        return _empty_result()
    panel = price_panel[valid_cols].copy()

    # --- ウェイトベクトル組み立て（valid_cols 内のみ・再正規化）---
    w_raw = {c: float(weights.get(c, 0.0)) for c in valid_cols}
    w_sum = sum(w_raw.values())
    if w_sum <= 0.0:
        w_vec = pd.Series({c: 1.0 / len(valid_cols) for c in valid_cols})
    else:
        w_vec = pd.Series({c: v / w_sum for c, v in w_raw.items()})

    # --- ポートフォリオ日次リターン ---
    daily_returns = cast(pd.DataFrame, panel.pct_change().dropna())
    port_ret = cast(pd.Series, (daily_returns * w_vec).sum(axis=1))

    # --- ベンチマーク日次リターン ---
    bench_ret: pd.Series = benchmark.pct_change().dropna()

    # --- 日付の積集合で揃える ---
    common_idx = port_ret.index.intersection(bench_ret.index)
    if len(common_idx) < 2:
        return _empty_result()
    port_ret = port_ret.loc[common_idx]
    bench_ret = bench_ret.loc[common_idx]

    # as_of を積集合後の最終日に更新
    as_of = str(common_idx[-1])

    # rebalance='monthly' は Phase 2 初期未実装（buy&hold と同じ挙動）
    # TODO(Phase 2+): 'monthly' の場合は月末にウェイトを再正規化するリバランスを実装

    port_stats = _compute_curve_stats(port_ret)
    bench_stats = _compute_curve_stats(bench_ret)

    excess_return = float(port_stats["annual_return"] - bench_stats["annual_return"])

    return {
        "as_of": as_of,
        "portfolio": port_stats,
        "benchmark": bench_stats,
        "excess_return": excess_return,
    }
