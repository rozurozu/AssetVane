"""quant.momentum の既知系列テスト（ADR-016: テスト済みコードで実装）。

設計の真実: docs/phase-specs/phase1-spec.md §4.3／§8。
DB に触れず手組み DataFrame で純関数を検証する（実 API も叩かない）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.quant.momentum import _wilder_rsi, compute_momentum


def _df(adj_close: list[float] | np.ndarray) -> pd.DataFrame:
    """date 昇順の最小 DataFrame を作る。"""
    n = len(adj_close)
    return pd.DataFrame(
        {"date": [f"2025-01-{i:03d}" for i in range(n)], "adj_close": list(adj_close)}
    )


def test_wilder_rsi_known_value() -> None:
    """Wilder RSI(14) を定番系列（Wilder/StockCharts 由来）で既知値 ±0.1 一致。

    確定式（§4.3.1: ewm alpha=1/14・adjust=False）での値に固定する。
    """
    prices = [
        44.34,
        44.09,
        44.15,
        43.61,
        44.33,
        44.83,
        45.10,
        45.42,
        45.84,
        46.08,
        45.89,
        46.03,
        45.61,
        46.28,
        46.28,
    ]
    rsi = _wilder_rsi(pd.Series(prices))
    assert abs(float(rsi.iloc[-1]) - 50.6574) < 0.1


def test_golden_cross_detected_with_high_score() -> None:
    """下降→上昇の V 字で最終日に sma25 が sma75 を上抜け → golden_cross=True・score>=0.6。"""
    full = np.concatenate([np.linspace(200, 80, 80), np.linspace(80, 260, 80)])
    df = _df(full[:108])  # 交差が起きる最終日 prefix（§8 探索で確定）
    result = compute_momentum(df)
    assert result is not None
    assert result["payload"]["golden_cross"] is True
    assert result["score"] >= 0.6


def test_golden_cross_not_detected_when_already_above() -> None:
    """ずっと上昇で sma25>sma75 が継続 → golden_cross=False（交差の瞬間のみ拾う）。"""
    df = _df(np.linspace(100, 200, 80))
    result = compute_momentum(df)
    assert result is not None
    assert result["payload"]["golden_cross"] is False


def test_rsi_reversal_detected() -> None:
    """上昇トレンド中の押し目で前日 RSI<30 → 当日 >=30 → rsi_reversal=True。"""
    base = np.linspace(100, 160, 80)
    arr = base.copy()
    arr[-7:-1] = np.linspace(arr[-8], arr[-8] - 20.0, 6)  # 押し目で oversold
    arr[-1] = arr[-2] + 4.0  # 最終日に 30 以上へ反転
    result = compute_momentum(_df(arr))
    assert result is not None
    assert result["payload"]["rsi_reversal"] is True


def test_insufficient_data_returns_none() -> None:
    """50 行（76 行未満）→ None。"""
    assert compute_momentum(_df(np.linspace(100, 110, 50))) is None


def test_adj_close_null_returns_none() -> None:
    """計算窓内に adj_close の null があれば None（前方補完せず skip＝§4.2）。"""
    arr = np.linspace(100.0, 150.0, 80)
    df = _df(arr)
    df.loc[40, "adj_close"] = np.nan
    assert compute_momentum(df) is None


def test_payload_shape_and_python_scalars() -> None:
    """戻り値 dict の形と payload が素の Python float であること（JSON 化・契約）。"""
    df = _df(np.linspace(100, 200, 80))
    result = compute_momentum(df)
    assert result is not None
    assert set(result.keys()) == {"date", "score", "payload"}
    assert "code" not in result and "signal_type" not in result
    assert isinstance(result["score"], float)
    payload = result["payload"]
    for key in (
        "trend",
        "gap",
        "golden_cross",
        "rsi_reversal",
        "notable",
        "sma25",
        "sma75",
        "rsi14",
        "adj_close",
        "label",
        "change_5d",
        "schema_version",
    ):
        assert key in payload
    assert isinstance(payload["sma25"], float)
    assert isinstance(payload["rsi14"], float)
    assert isinstance(payload["label"], str)
