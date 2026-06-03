"""volume_spike シグナル — 出来高急増（過去20営業日平均比）。

設計の真実: docs/phase-specs/phase1-spec.md §4.4（確定パラメータ・式・payload 例）。

- **純関数・DB 非依存**（ADR-016）。入力 DataFrame → 出力 dict/None。既知系列テストで固定。
- 出来高は**未調整 `volume`** のまま使う（spike は比率なので区間内で係数が揃えば概ね保たれる）。
  分割をまたぐ窓は `adj_close` の段差から `adj_warning` を立てる（§4.2）。
- score は連続値（0..1）。notable/保存フロアは破壊的ゲートではなく目印＋足切り（ADR-026）。
- パラメータは名前付きモジュール定数（env 不可・将来 `method_settings`＝ADR-027）。
"""

from __future__ import annotations

from typing import Any

import pandas as pd

# 開始既定の名前付き定数（U-2・ADR-027）。env 不可・将来 method_settings へ。
VOLUME_FLOOR = 1.5  # 保存フロア（ratio がこれ未満なら None＝保存しない）
NOTABLE_RATIO = 3.0  # notable 閾値（平常の 3 倍を「急増」と目印。破壊的ゲートではない）
MIN_VOL_MA20 = 50_000.0  # 出来高フロア（vol_ma20 がこれ未満なら低流動性として除外）
_SPLIT_WARN_RATIO = 0.5  # 窓内 adj_close の段差比がこれを超えたら分割警告

_VOL_WINDOW = 20  # 基準平均の窓（過去20営業日・当日除く）
_MIN_ROWS = 21  # 最低データ長（当日 + 過去20日）

_SCHEMA_VERSION = 1


def compute_volume_spike(quotes: pd.DataFrame) -> dict[str, Any] | None:
    """1 銘柄の日足から出来高急増シグナルを 1 件算出（最新日基準）。

    quotes: columns=[date, volume, adj_close]（date 昇順）。戻り値は signals 用 dict
    `{"date", "score", "payload"}`、不成立/不足/adj_close 欠損なら None。
    （ADR-016: 純関数・DB 非依存）

    戻り値の `code`/`signal_type` は付けない（呼び出し側 calc_signals が付与）。
    """
    if quotes is None or len(quotes) < _MIN_ROWS:
        return None
    for col in ("date", "volume", "adj_close"):
        if col not in quotes.columns:
            return None

    # pandas の __getitem__ はユニオン型を返すため Series に固定（pandas-stubs 無し環境）。
    volume: pd.Series = pd.Series(quotes["volume"])
    adj: pd.Series = pd.Series(quotes["adj_close"])
    # adj_close 欠損は skip（§4.2・分割判定にも adj_close を使うため）。
    if bool(adj.isna().any()) or bool(volume.isna().any()):
        return None

    # 基準平均は当日を除く過去20営業日（shift(1) で当日を外す）。
    vol_ma20: pd.Series = pd.Series(volume.shift(1).rolling(_VOL_WINDOW).mean())
    last_ma = vol_ma20.iloc[-1]
    if pd.isna(last_ma) or last_ma <= 0:
        return None

    # 低流動性除外（vol_ma20 が出来高フロア未満なら保存しない）。
    if last_ma < MIN_VOL_MA20:
        return None

    last_volume = float(volume.iloc[-1])
    ratio = last_volume / float(last_ma)

    # 保存フロア未満は None（near-miss は残すがフロア未満は保存しない＝ADR-026）。
    if ratio < VOLUME_FLOOR:
        return None

    score = min(ratio / 10.0, 1.0)

    # 分割警告: 直近窓（当日+過去20日）の adj_close 隣接段差が大きければ立てる。
    window_adj = adj.iloc[-_MIN_ROWS:]
    rel_step = (window_adj / window_adj.shift(1) - 1.0).abs()
    adj_warning = bool(rel_step.max() >= _SPLIT_WARN_RATIO)

    # 5 日騰落率（符号付き）。窓不足なら None。
    change_5d: float | None = None
    if len(adj) >= 6:
        base = adj.iloc[-6]
        if not pd.isna(base) and base != 0:
            change_5d = float(adj.iloc[-1] / base - 1.0)

    notable = bool(ratio >= NOTABLE_RATIO)
    label = f"出来高 平常の{ratio:.1f}倍"

    payload: dict[str, Any] = {
        "volume": float(last_volume),
        "vol_ma20": float(last_ma),
        "ratio": float(ratio),
        "notable": notable,
        "adj_warning": adj_warning,
        "label": label,
        "change_5d": change_5d,
        "schema_version": _SCHEMA_VERSION,
    }
    return {
        "date": str(quotes["date"].iloc[-1]),
        "score": float(score),
        "payload": payload,
    }
