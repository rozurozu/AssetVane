"""quant 内で共有する DataFrame 小ヘルパ（純関数・#22）。

optimize / portfolio / backtest に逐語コピーされていた `_column_has_nulls` を 1 箇所へ集約する
（同一実装の 3 重複＝ドリフト源）。DB を知らない純関数（ADR-016）。
"""

from __future__ import annotations

from typing import cast

import pandas as pd


def column_has_nulls(frame: pd.DataFrame, column: str) -> bool:
    """Pandas の列取得が Series/DataFrame どちらでも null 有無を bool で返す。

    重複銘柄コード等で `frame[column]` が DataFrame を返すことがあるため両対応する。
    """
    values = frame[column]
    if isinstance(values, pd.DataFrame):
        return bool(values.isna().to_numpy().any())
    series = cast(pd.Series, values)
    return bool(series.isna().any())
