"""quant.volume_spike の既知系列テスト（ADR-016: テスト済みコードで実装）。

設計の真実: docs/phase-specs/phase1-spec.md §4.4／§8。
DB に触れず手組み DataFrame で純関数を検証する（実 API も叩かない）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.quant.volume_spike import compute_volume_spike


def _df(volume: list[float], adj_close: list[float]) -> pd.DataFrame:
    """date 昇順の最小 DataFrame を作る。"""
    n = len(volume)
    return pd.DataFrame(
        {
            "date": [f"2025-01-{i:03d}" for i in range(n)],
            "volume": volume,
            "adj_close": adj_close,
        }
    )


def test_volume_spike_ratio_4_gives_score_04() -> None:
    """平常 100 万株 ×20 日 ＋当日 400 万株 → ratio=4.0・score=0.4・notable=True。"""
    volume = [1_000_000.0] * 20 + [4_000_000.0]
    result = compute_volume_spike(_df(volume, [100.0] * 21))
    assert result is not None
    assert abs(result["payload"]["ratio"] - 4.0) < 1e-9
    assert abs(result["score"] - 0.4) < 1e-9
    assert result["payload"]["notable"] is True
    assert result["payload"]["label"] == "出来高 平常の4.0倍"


def test_low_liquidity_excluded() -> None:
    """vol_ma20=3 万株（出来高フロア 5 万未満）→ None。"""
    volume = [30_000.0] * 20 + [120_000.0]
    assert compute_volume_spike(_df(volume, [100.0] * 21)) is None


def test_below_save_floor_returns_none() -> None:
    """ratio=1.2（保存フロア 1.5 未満）→ None。"""
    volume = [1_000_000.0] * 20 + [1_200_000.0]
    assert compute_volume_spike(_df(volume, [100.0] * 21)) is None


def test_adj_close_null_returns_none() -> None:
    """窓内 adj_close の null → None（§4.2）。"""
    adj = [100.0] * 21
    adj[5] = np.nan
    volume = [1_000_000.0] * 20 + [4_000_000.0]
    assert compute_volume_spike(_df(volume, adj)) is None


def test_insufficient_data_returns_none() -> None:
    """20 行（21 行未満）→ None。"""
    assert compute_volume_spike(_df([1_000_000.0] * 20, [100.0] * 20)) is None


def test_notable_false_below_threshold() -> None:
    """ratio=2.0（保存フロア超だが notable 閾値 3.0 未満）→ score=0.2・notable=False。"""
    volume = [1_000_000.0] * 20 + [2_000_000.0]
    result = compute_volume_spike(_df(volume, [100.0] * 21))
    assert result is not None
    assert abs(result["payload"]["ratio"] - 2.0) < 1e-9
    assert abs(result["score"] - 0.2) < 1e-9
    assert result["payload"]["notable"] is False


def test_payload_shape_and_python_scalars() -> None:
    """戻り値 dict の形と payload が素の Python スカラであること（JSON 化・契約）。"""
    volume = [1_000_000.0] * 20 + [4_000_000.0]
    result = compute_volume_spike(_df(volume, [100.0] * 21))
    assert result is not None
    assert set(result.keys()) == {"date", "score", "payload"}
    assert "code" not in result and "signal_type" not in result
    assert isinstance(result["score"], float)
    payload = result["payload"]
    for key in (
        "volume",
        "vol_ma20",
        "ratio",
        "notable",
        "adj_warning",
        "label",
        "change_5d",
        "schema_version",
    ):
        assert key in payload
    assert isinstance(payload["ratio"], float)
    assert isinstance(payload["adj_warning"], bool)
    assert isinstance(payload["label"], str)
