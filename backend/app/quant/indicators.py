"""指標オンザフライ計算 — SMA25/75・Wilder RSI(14)・出来高 MA20（最新日基準）。

設計の真実: docs/phase-specs/phase3-spec.md §4.4（get_indicators 返却）・§4.5・ADR-016。

- **純関数・DB 非依存**（ADR-016）。入力 DataFrame → 出力 dict。get_indicators Tool が呼ぶ。
- すべて `adj_close`（調整後終値）で計算（分割の段差を除去）。欠損や不足は該当値を None に。
- **数字を作らない**（ADR-014）。データ不足・欠損で計算できない指標は None で返す。
- RSI は momentum.py の `_wilder_rsi`（Wilder 平滑・period14）と同一式を再利用（手法の一貫性）。
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from app.quant.momentum import _wilder_rsi

_SMA_SHORT = 25  # 短期 SMA 日数
_SMA_LONG = 75  # 長期 SMA 日数
_VOL_MA = 20  # 出来高移動平均の日数


def _last_or_none(series: pd.Series) -> float | None:
    """series の最終値を素の float で返す。NaN・空なら None（数字を作らない＝ADR-014）。"""
    if series is None or len(series) == 0:
        return None
    value = series.iloc[-1]
    if pd.isna(value):
        return None
    return float(value)


def compute_indicators(quotes: pd.DataFrame) -> dict[str, Any]:
    """1 銘柄の日足から最新日の指標を算出する（get_indicators Tool 用・spec §4.4）。

    quotes: columns=[date, adj_close, volume]（date 昇順）。戻り値は平坦な dict
    `{as_of, adj_close, sma25, sma75, rsi14, vol_ma20}`。データ不足・欠損の指標は None。
    （ADR-016: 純関数・DB 非依存／ADR-014: 計算できない値は捏造せず None）

    `as_of`/`adj_close`/`is_delayed` は handler が補う（ここは指標の事実だけ返す）。
    """
    empty: dict[str, Any] = {
        "as_of": None,
        "adj_close": None,
        "sma25": None,
        "sma75": None,
        "rsi14": None,
        "vol_ma20": None,
    }
    if quotes is None or len(quotes) == 0:
        return empty
    if "date" not in quotes.columns or "adj_close" not in quotes.columns:
        return empty

    # pandas の __getitem__ はユニオン型を返すため Series に固定（pandas-stubs 無し環境）。
    adj: pd.Series = pd.Series(quotes["adj_close"], dtype=float)

    as_of = str(quotes["date"].iloc[-1])
    adj_close = _last_or_none(adj)

    # adj_close に欠損があると rolling 平均がずれるため、欠損時は SMA/RSI を None にする。
    if bool(adj.isna().any()):
        sma25 = sma75 = rsi14 = None
    else:
        sma25 = _last_or_none(pd.Series(adj.rolling(_SMA_SHORT).mean()))
        sma75 = _last_or_none(pd.Series(adj.rolling(_SMA_LONG).mean()))
        rsi14 = _last_or_none(_wilder_rsi(adj))

    # 出来高 MA20。volume 列が無い・欠損があるなら None（数字を作らない）。
    vol_ma20: float | None = None
    if "volume" in quotes.columns:
        vol: pd.Series = pd.Series(quotes["volume"], dtype=float)
        if not bool(vol.isna().any()):
            vol_ma20 = _last_or_none(pd.Series(vol.rolling(_VOL_MA).mean()))

    return {
        "as_of": as_of,
        "adj_close": adj_close,
        "sma25": sma25,
        "sma75": sma75,
        "rsi14": rsi14,
        "vol_ma20": vol_ma20,
    }
