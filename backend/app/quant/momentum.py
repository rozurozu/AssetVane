"""momentum シグナル — SMA25/75・Wilder RSI(14)・ゴールデンクロス／RSI 反転。

設計の真実: docs/phase-specs/phase1-spec.md §4.3／§4.3.1（確定パラメータ・式・payload 例）。

- **純関数・DB 非依存**（ADR-016）。入力 DataFrame → 出力 dict/None。既知系列テストで固定。
- すべて `adj_close`（調整後終値）で計算（分割の段差を除去＝§4.2）。欠損は skip（None）。
- score は「今日クロスしたか(0/1)」のイベント値ではなく**連続の上昇トレンド強度**（0..1）。
  near-miss を濃淡で残し AI が判断材料に使える（閾値で材料を捨てない＝ADR-026）。
- パラメータは名前付きモジュール定数（env 不可・将来 `method_settings`＝ADR-027）。
"""

from __future__ import annotations

from typing import Any

import pandas as pd

# 開始既定の名前付き定数（U-1・ADR-027）。env 不可・将来 method_settings へ。
TREND_BAND = 0.05  # gap=±TREND_BAND で trend が 0/1 に飽和する帯幅
RSI_LOW = 30  # RSI 正規化の下端（oversold 目安）
RSI_HIGH = 70  # RSI 正規化の上端（overbought 目安）
W_TREND = 0.6  # トレンド項の重み
W_RSI = 0.4  # RSI 項の重み
GC_BOOST = 0.15  # ゴールデンクロス当日の加点ブースター
REV_BOOST = 0.15  # RSI 反転当日の加点ブースター
MOMENTUM_FLOOR = 0.3  # 保存フロア（score がこれ未満なら None＝保存しない）

_SMA_SHORT = 25  # 短期 SMA 日数
_SMA_LONG = 75  # 長期 SMA 日数
_RSI_PERIOD = 14  # Wilder RSI の期間
_MIN_ROWS = 76  # 最低データ長（SMA75 と前日比較に必要）

_SCHEMA_VERSION = 1


def _wilder_rsi(adj_close: pd.Series) -> pd.Series:
    """Wilder 平滑 RSI(14)（§4.3.1）。TA-Lib・各証券会社チャートの既定と一致。"""
    delta = adj_close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    # Wilder 平滑＝ewm(alpha=1/period, adjust=False)。min_periods で立ち上がりを NaN に。
    avg_gain = gain.ewm(alpha=1 / _RSI_PERIOD, adjust=False, min_periods=_RSI_PERIOD).mean()
    avg_loss = loss.ewm(alpha=1 / _RSI_PERIOD, adjust=False, min_periods=_RSI_PERIOD).mean()
    rs = avg_gain / avg_loss
    rsi: pd.Series = pd.Series(100 - 100 / (1 + rs))
    # avg_loss==0（全勝区間）は rs=inf → rsi=100。ゼロ割を埋める。
    rsi = rsi.where(avg_loss != 0, 100.0)
    return rsi


def _clip01(value: float) -> float:
    """0..1 にクリップした素の Python float を返す。"""
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return float(value)


def compute_momentum(quotes: pd.DataFrame) -> dict[str, Any] | None:
    """1 銘柄の日足から momentum シグナルを 1 件算出（最新日基準）。

    quotes: columns=[date, adj_close]（date 昇順）。戻り値は signals 用 dict
    `{"date", "score", "payload"}`、不成立/データ不足/adj_close 欠損なら None。
    （ADR-016: 純関数・DB 非依存・docs/data-model.md §4）

    戻り値の `code`/`signal_type` は付けない（呼び出し側 calc_signals が付与）。
    """
    if quotes is None or len(quotes) < _MIN_ROWS:
        return None
    if "adj_close" not in quotes.columns or "date" not in quotes.columns:
        return None

    # pandas の __getitem__ はユニオン型を返すため Series に固定（pandas-stubs 無し環境）。
    adj: pd.Series = pd.Series(quotes["adj_close"])
    # adj_close 欠損は前方補完せず skip（数字を作らない＝ADR-014・§4.2）。
    if bool(adj.isna().any()):
        return None

    sma25: pd.Series = pd.Series(adj.rolling(_SMA_SHORT).mean())
    sma75: pd.Series = pd.Series(adj.rolling(_SMA_LONG).mean())
    rsi = _wilder_rsi(adj)

    # 最新日（最終行）の各指標。これらが NaN なら算出不可。
    last_sma25 = sma25.iloc[-1]
    last_sma75 = sma75.iloc[-1]
    last_rsi = rsi.iloc[-1]
    if pd.isna(last_sma25) or pd.isna(last_sma75) or pd.isna(last_rsi):
        return None

    # ゴールデンクロス: 当日 sma25>sma75 かつ前日 sma25<=sma75（瞬間のみ拾う）。
    prev_sma25 = sma25.iloc[-2]
    prev_sma75 = sma75.iloc[-2]
    golden_cross = bool(
        not pd.isna(prev_sma25)
        and not pd.isna(prev_sma75)
        and prev_sma25 <= prev_sma75
        and last_sma25 > last_sma75
    )

    # RSI 反転（買い）: 前日 RSI<RSI_LOW → 当日 >=RSI_LOW。
    prev_rsi = rsi.iloc[-2]
    rsi_reversal = bool(not pd.isna(prev_rsi) and prev_rsi < RSI_LOW and last_rsi >= RSI_LOW)

    # 連続スコア（§4.3）。gap=0(クロス点)→trend=0.5、±TREND_BAND で 0/1 飽和。
    gap = (last_sma25 - last_sma75) / last_sma75
    trend = _clip01(0.5 + gap / (2 * TREND_BAND))
    rsi_norm = _clip01((last_rsi - RSI_LOW) / (RSI_HIGH - RSI_LOW))
    score = _clip01(
        W_TREND * trend
        + W_RSI * rsi_norm
        + GC_BOOST * (1.0 if golden_cross else 0.0)
        + REV_BOOST * (1.0 if rsi_reversal else 0.0)
    )

    # 保存フロア未満は None（near-miss は残すがフロア未満は保存しない＝ADR-026）。
    if score < MOMENTUM_FLOOR:
        return None

    # 5 日騰落率（符号付き・最新 / 5 営業日前 - 1）。窓不足なら None。
    change_5d: float | None = None
    if len(adj) >= 6:
        base = adj.iloc[-6]
        if not pd.isna(base) and base != 0:
            change_5d = float(adj.iloc[-1] / base - 1.0)

    notable = bool(golden_cross or score >= 0.6)
    if golden_cross:
        label = "SMA25/75 ゴールデンクロス"
    elif rsi_reversal:
        label = "RSI が売られすぎから反転"
    else:
        label = "上昇トレンド継続"

    payload: dict[str, Any] = {
        "trend": float(trend),
        "gap": float(gap),
        "golden_cross": golden_cross,
        "rsi_reversal": rsi_reversal,
        "notable": notable,
        "sma25": float(last_sma25),
        "sma75": float(last_sma75),
        "rsi14": float(last_rsi),
        "adj_close": float(adj.iloc[-1]),
        "label": label,
        "change_5d": change_5d,
        "schema_version": _SCHEMA_VERSION,
    }
    return {
        "date": str(quotes["date"].iloc[-1]),
        "score": float(score),
        "payload": payload,
    }
