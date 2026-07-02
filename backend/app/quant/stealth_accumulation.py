"""stealth_accum シグナル — 機関投資家のステルス仕込み（価格圧縮 × 出来高“持続”増）。

設計の真実: docs/decisions.md ADR-074（機関のステルス仕込みを日足シグナル化・VWAP 分足は採らない）。

- **純関数・DB 非依存**（ADR-016）。入力 DataFrame＋market_cap → 出力 dict/None。既知系列で固定。
- **volume_spike（単日の出来高急増）とは別物**＝直近 W 日の「価格が横ばい（圧縮）なのに出来高が
  “持続的”に増える」Wyckoff の accumulation base を捉える。単日 spike ではなく短期 MA/長期 MA の比。
- OHLC は adj_close/close の比で分割調整して使う（raw と adj を混ぜない＝lead_lag 同思想）。
- **数字を作らない**（ADR-014）。データ不足・欠損・時価総額フロア未満・低流動性は None で返す。
- payload.phase＝`in_range`（レンジ内で仕込み継続）/`breakout`（出来高伴いレンジ上放れ）。
  発火は「仕込み検出＋phase フラグ」の一本（ADR-074）。下放れ（レンジ割れ）は None。
- 時価総額フロア（500 億）で仕手筋を除外（動画の「まともな機関は小型に入れない」）。
- パラメータは名前付きモジュール定数（env 不可・将来 method_settings＝ADR-027）。
"""

from __future__ import annotations

from typing import Any

import pandas as pd

# 開始既定の名前付き定数（ADR-027）。env 不可・将来 method_settings へ。
_W = 20  # 仕込み窓（直近 W 営業日のレンジ圧縮を見る）
_BASELINE = 60  # 出来高ベースライン窓（長期 MA・“持続”増の分母）
_MIN_ROWS = 61  # 最低データ長（BASELINE + 当日）

RANGE_MAX = 0.12  # 圧縮ゲート: 直近 W 日 close の値幅/平均がこれ未満なら「横ばい」
VOL_ELEV_MIN = 1.3  # 出来高持続増ゲート: MA(vol,W)/MA(vol,BASELINE) がこれ以上
MARKET_CAP_FLOOR = 5.0e10  # 時価総額フロア（500 億円・仕手除外＝ADR-074）
MIN_VOL_MA = 50_000.0  # 低流動性除外（長期出来高 MA がこれ未満なら除外・volume_spike と同思想）
WICK_MIN_RATIO = 0.5  # 長い下ひげ判定: 下ひげ/レンジ がこれ以上の日を「下支え」と数える
WICK_COUNT_MIN = 3  # 窓内で下支えが WICK_COUNT_MIN 日以上あれば加点

STEALTH_FLOOR = 0.4  # 保存フロア（score がこれ未満なら None＝保存しない・ADR-026）
_W_COMPRESS = 0.5  # 圧縮の強さの重み
_W_VOL = 0.4  # 出来高増の強さの重み
_WICK_BONUS = 0.1  # 下支え加点
_BREAKOUT_BOOST = 0.15  # レンジ上放れ当日の加点

_SCHEMA_VERSION = 1


def _clip01(value: float) -> float:
    """0..1 にクリップした素の Python float を返す。"""
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return float(value)


def compute_stealth_accumulation(
    quotes: pd.DataFrame, market_cap: float | None
) -> dict[str, Any] | None:
    """1 銘柄の日足＋時価総額からステルス仕込みシグナルを 1 件算出（最新日基準・ADR-074）。

    quotes: columns=[date, open, high, low, close, adj_close, volume]（date 昇順）。
    market_cap: 最新の時価総額（円）。None/フロア未満は除外。
    戻り値は signals 用 dict `{"date", "score", "payload"}`、不成立/不足/欠損なら None
    （ADR-016: 純関数・DB 非依存）。`code`/`signal_type` は呼び出し側 calc_signals が付与。
    """
    if quotes is None or len(quotes) < _MIN_ROWS:
        return None
    needed = ("date", "open", "high", "low", "close", "adj_close", "volume")
    for col in needed:
        if col not in quotes.columns:
            return None

    # 時価総額フロア（仕手除外）。未取得（None）も落とす＝事実が無い銘柄には出さない。
    if market_cap is None or float(market_cap) < MARKET_CAP_FLOOR:
        return None

    # pandas の __getitem__ はユニオン型を返すため Series に固定（pandas-stubs 無し環境）。
    adj: pd.Series = pd.Series(quotes["adj_close"], dtype=float)
    close: pd.Series = pd.Series(quotes["close"], dtype=float)
    high: pd.Series = pd.Series(quotes["high"], dtype=float)
    low: pd.Series = pd.Series(quotes["low"], dtype=float)
    open_: pd.Series = pd.Series(quotes["open"], dtype=float)
    volume: pd.Series = pd.Series(quotes["volume"], dtype=float)

    # 欠損は補完せず skip（数字を作らない＝ADR-014）。分割調整に close/adj_close 双方が要る。
    for series in (adj, close, high, low, open_, volume):
        if bool(series.isna().any()):
            return None
    if bool((close <= 0).any()):
        return None

    # 出来高 MA（短期 W / 長期 BASELINE）。持続増＝短期が長期を VOL_ELEV_MIN 倍以上上回る。
    # pd.Series 包みは rolling().mean() の型固定（pandas-stubs 無し・volume_spike と同じ）。
    vol_ma_short = float(pd.Series(volume.rolling(_W).mean()).iloc[-1])
    vol_ma_long = float(pd.Series(volume.rolling(_BASELINE).mean()).iloc[-1])
    if vol_ma_long <= 0:
        return None
    # 低流動性除外（長期出来高 MA がフロア未満なら保存しない）。
    if vol_ma_long < MIN_VOL_MA:
        return None
    vol_elevation = vol_ma_short / vol_ma_long
    if vol_elevation < VOL_ELEV_MIN:
        return None

    # 仕込みレンジ（当日を除く直近 W 日の adj_close）。ceiling/floor/mean を取る。
    base = adj.iloc[-(_W + 1) : -1]
    ceiling = float(base.max())
    floor_ = float(base.min())
    mean_ = float(base.mean())
    if mean_ <= 0:
        return None
    range_ratio = (ceiling - floor_) / mean_
    # 圧縮ゲート: レンジが狭くないと「仕込み」ではない。
    if range_ratio >= RANGE_MAX:
        return None

    today = float(adj.iloc[-1])
    # phase 判定: 上放れ / レンジ内 / 下放れ（下放れは仕込み失敗＝None）。
    if today > ceiling:
        phase = "breakout"
    elif today >= floor_:
        phase = "in_range"
    else:
        return None

    # 上放れの出来高確認（当日出来高が長期平均以上か）。動画の「上放れ＋出来高」。
    today_volume = float(volume.iloc[-1])
    volume_confirmed = bool(phase == "breakout" and today_volume >= vol_ma_long)

    # 長い下ひげ（下支え）を仕込み窓で数える。OHLC は adj_close/close 比で分割調整して使う。
    factor = adj / close  # close>0 は上でガード済み
    adj_high = (high * factor).iloc[-(_W + 1) : -1]
    adj_low = (low * factor).iloc[-(_W + 1) : -1]
    adj_open = (open_ * factor).iloc[-(_W + 1) : -1]
    adj_base_close = adj.iloc[-(_W + 1) : -1]
    rng = adj_high - adj_low
    body_low = pd.concat([adj_open, adj_base_close], axis=1).min(axis=1)
    lower_wick = body_low - adj_low
    wick_ratio = (lower_wick / rng).where(rng > 0, 0.0)
    wick_days = int((wick_ratio >= WICK_MIN_RATIO).sum())

    # 連続スコア（0..1）。圧縮の強さ × 出来高増の強さ ＋ 下支え加点 ＋ 上放れ加点。
    compression_strength = _clip01((RANGE_MAX - range_ratio) / RANGE_MAX)
    vol_strength = _clip01(vol_elevation - 1.0)
    wick_bonus = _WICK_BONUS if wick_days >= WICK_COUNT_MIN else 0.0
    breakout_boost = _BREAKOUT_BOOST if phase == "breakout" else 0.0
    score = _clip01(
        _W_COMPRESS * compression_strength + _W_VOL * vol_strength + wick_bonus + breakout_boost
    )
    if score < STEALTH_FLOOR:
        return None

    # notable: 上放れ（出来高確認あり）か、圧縮強い高スコアを目印にする（notable.py が読む）。
    notable = bool((phase == "breakout" and volume_confirmed) or score >= 0.7)

    if phase == "breakout":
        label = f"レンジ上放れ（出来高 {vol_elevation:.1f} 倍・仕込み後）"
    else:
        label = f"仕込み継続中（価格圧縮 × 出来高 {vol_elevation:.1f} 倍）"

    # 5 日騰落率（符号付き）。窓不足なら None。
    change_5d: float | None = None
    if len(adj) >= 6:
        b = float(adj.iloc[-6])
        if b != 0:
            change_5d = float(today / b - 1.0)

    payload: dict[str, Any] = {
        "phase": phase,
        "range_ratio": float(range_ratio),
        "vol_elevation": float(vol_elevation),
        "vol_ma_baseline": float(vol_ma_long),
        "wick_days": wick_days,
        "market_cap": float(market_cap),
        "ceiling": ceiling,
        "floor": floor_,
        "close": today,
        "volume_confirmed": volume_confirmed,
        "notable": notable,
        "label": label,
        "change_5d": change_5d,
        "schema_version": _SCHEMA_VERSION,
    }
    return {
        "date": str(quotes["date"].iloc[-1]),
        "score": float(score),
        "payload": payload,
    }
