"""stealth_accum 純関数の振る舞いを固定する（ADR-074・機関のステルス仕込み）。

担保: in_range/breakout の phase 判定・圧縮/出来高/時価総額/低流動性の各ゲートが None・
下ひげ加点・下放れは None。ADR-016（純関数・DB 非依存）/ADR-014（数字を作らない）。
"""

from __future__ import annotations

import pandas as pd

from app.quant.stealth_accumulation import (
    MARKET_CAP_FLOOR,
    compute_stealth_accumulation,
)

_BIG_CAP = 8.0e10  # 800 億円（フロア 500 億超）


def _make_df(
    closes: list[float],
    volumes: list[float],
    *,
    lows: list[float] | None = None,
) -> pd.DataFrame:
    """OHLC 日足 DataFrame を組む。既定は open=close・high=close+2・low=close-2・adj=close。"""
    n = len(closes)
    dates = pd.date_range("2023-01-01", periods=n, freq="D").astype(str)
    lo = lows if lows is not None else [c - 2.0 for c in closes]
    return pd.DataFrame(
        {
            "date": dates,
            "open": [float(c) for c in closes],
            "high": [float(c) + 2.0 for c in closes],
            "low": [float(x) for x in lo],
            "close": [float(c) for c in closes],
            "adj_close": [float(c) for c in closes],
            "volume": [float(v) for v in volumes],
        }
    )


def _base_pattern(n: int = 80) -> tuple[list[float], list[float]]:
    """基準の仕込みパターン: 60 日フラット→直近 20 日は価格圧縮（997/1000/1003）＋出来高 1.8 倍。"""
    closes = [1000.0] * 60 + [1000.0 + (i % 3 - 1) * 3.0 for i in range(20)]
    volumes = [100_000.0] * 60 + [180_000.0] * 20
    return closes[:n], volumes[:n]


def test_in_range_detected():
    """圧縮×出来高持続増でレンジ内なら phase=in_range で発火する。"""
    closes, volumes = _base_pattern()
    result = compute_stealth_accumulation(_make_df(closes, volumes), _BIG_CAP)
    assert result is not None
    assert result["payload"]["phase"] == "in_range"
    assert result["payload"]["vol_elevation"] >= 1.3
    assert result["payload"]["range_ratio"] < 0.12
    assert 0.0 < result["score"] <= 1.0


def test_breakout_detected():
    """仕込み後にレンジ上限を出来高伴いで上抜けたら phase=breakout・volume_confirmed。"""
    closes, volumes = _base_pattern()
    closes[-1] = 1030.0  # レンジ上限（~1003）を明確に上抜け
    volumes[-1] = 400_000.0  # 上放れ当日の大商い
    result = compute_stealth_accumulation(_make_df(closes, volumes), _BIG_CAP)
    assert result is not None
    assert result["payload"]["phase"] == "breakout"
    assert result["payload"]["volume_confirmed"] is True
    assert result["payload"]["notable"] is True


def test_not_compressed_returns_none():
    """価格が横ばいでなく値幅が広い（トレンド）なら仕込みでない＝None。"""
    closes = [1000.0] * 60 + [900.0 + i * 12.0 for i in range(20)]  # 直近が大きく上昇
    volumes = [100_000.0] * 60 + [180_000.0] * 20
    assert compute_stealth_accumulation(_make_df(closes, volumes), _BIG_CAP) is None


def test_volume_not_elevated_returns_none():
    """圧縮していても出来高が持続増していなければ仕込みでない＝None。"""
    closes, _ = _base_pattern()
    volumes = [100_000.0] * 80  # フラットな出来高（elevation ~1.0）
    assert compute_stealth_accumulation(_make_df(closes, volumes), _BIG_CAP) is None


def test_small_cap_excluded():
    """時価総額フロア未満（仕手筋レンジ）は除外＝None。"""
    closes, volumes = _base_pattern()
    small = MARKET_CAP_FLOOR - 1.0
    assert compute_stealth_accumulation(_make_df(closes, volumes), small) is None


def test_market_cap_none_excluded():
    """時価総額が未取得（None）なら事実が無いので出さない＝None。"""
    closes, volumes = _base_pattern()
    assert compute_stealth_accumulation(_make_df(closes, volumes), None) is None


def test_low_liquidity_returns_none():
    """長期出来高 MA が低流動性フロア未満なら除外＝None。"""
    closes, _ = _base_pattern()
    volumes = [1_000.0] * 60 + [1_800.0] * 20  # 圧倒的に薄い
    assert compute_stealth_accumulation(_make_df(closes, volumes), _BIG_CAP) is None


def test_insufficient_rows_returns_none():
    """最低データ長（61 行）未満は None。"""
    closes = [1000.0] * 30
    volumes = [180_000.0] * 30
    assert compute_stealth_accumulation(_make_df(closes, volumes), _BIG_CAP) is None


def test_breakdown_returns_none():
    """レンジ下限を割った（下放れ＝仕込み失敗/分配）なら None。"""
    closes, volumes = _base_pattern()
    closes[-1] = 950.0  # レンジ下限（~997）を明確に下抜け
    assert compute_stealth_accumulation(_make_df(closes, volumes), _BIG_CAP) is None


def test_lower_wick_bonus_counts():
    """直近窓で長い下ひげ（下支え）が続くと wick_days に数えられる。"""
    closes, volumes = _base_pattern()
    # 直近 20 日のうち複数日で low を大きく下げ、下ひげを作る（body_low - low が範囲の半分超）。
    lows = [c - 2.0 for c in closes]
    for i in range(60, 75):  # 15 日ぶん長い下ひげを付ける
        lows[i] = closes[i] - 40.0
    result = compute_stealth_accumulation(_make_df(closes, volumes, lows=lows), _BIG_CAP)
    assert result is not None
    assert result["payload"]["wick_days"] >= 3
