"""infer.score_all の検証 — 決定性・パーセンタイル正規化・payload・NaN 安全化（Phase 5）。

担保（phase5-spec.md §4.3/§9・ADR-016）:
- 実 tiny LightGBM で同入力 2 回 → 同じ score（決定性）。payload に予測値/モデル版/特徴量スナップ。
- 予測値の当日内パーセンタイル順位が score（0..1・最大=1.0）。
- feature_snapshot の NaN は JSON 不正なので None に倒す。空入力は []。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.quant.ml.features import FEATURE_NAMES
from app.quant.ml.infer import score_all
from app.quant.ml.train import train_model


def _matrix(codes: list[str], rng: np.random.Generator) -> pd.DataFrame:
    """index=code・columns=FEATURE_NAMES の特徴量行列を作る。"""
    data = rng.normal(size=(len(codes), len(FEATURE_NAMES)))
    return pd.DataFrame(data, index=codes, columns=list(FEATURE_NAMES))


class _StubModel:
    """predict が固定配列を返すスタブ（正規化・NaN の検証用）。"""

    def __init__(self, preds: list[float]) -> None:
        self._preds = np.asarray(preds, dtype=float)

    def predict(self, x: pd.DataFrame) -> np.ndarray:
        return self._preds


def test_score_all_deterministic() -> None:
    """実 LightGBM で同じ入力 2 回 → score 完全一致。payload 構造も確認。"""
    rng = np.random.default_rng(1)
    train_x = pd.DataFrame(rng.normal(size=(50, len(FEATURE_NAMES))), columns=list(FEATURE_NAMES))
    train_y = pd.Series(train_x.to_numpy() @ rng.normal(size=len(FEATURE_NAMES)))
    model, _ = train_model(
        train_x, train_y, list(FEATURE_NAMES), params={"n_estimators": 20, "num_leaves": 7}
    )

    fm = _matrix([f"{i:04d}0" for i in range(8)], rng)
    a = score_all(model, list(FEATURE_NAMES), fm, as_of="2025-06-01", model_version="ai_alpha-test")
    b = score_all(model, list(FEATURE_NAMES), fm, as_of="2025-06-01", model_version="ai_alpha-test")
    assert [r["score"] for r in a] == [r["score"] for r in b]

    r0 = a[0]
    assert r0["signal_type"] == "ai_alpha"
    assert r0["date"] == "2025-06-01"
    assert r0["payload"]["model_version"] == "ai_alpha-test"
    assert r0["payload"]["schema_version"] == 1
    assert set(r0["payload"]["feature_snapshot"].keys()) == set(FEATURE_NAMES)
    assert "predicted_excess_return_60d" in r0["payload"]


def test_score_all_percentile_normalization() -> None:
    """予測 [10,30,20] → score は当日内パーセンタイル順位（最大=1.0）。"""
    rng = np.random.default_rng(2)
    fm = _matrix(["A", "B", "C"], rng)
    model = _StubModel([10.0, 30.0, 20.0])
    rows = score_all(model, list(FEATURE_NAMES), fm, as_of="2025-06-01", model_version="v")
    scores = {r["code"]: r["score"] for r in rows}
    assert scores["A"] == 1 / 3
    assert scores["B"] == 1.0  # 最大
    assert scores["C"] == 2 / 3


def test_score_all_nan_snapshot_to_none() -> None:
    """feature_snapshot の NaN は None に倒す（JSON 安全）。"""
    rng = np.random.default_rng(3)
    fm = _matrix(["A"], rng)
    fm.iloc[0, 0] = np.nan  # 最初の特徴量を欠損に
    model = _StubModel([0.5])
    rows = score_all(model, list(FEATURE_NAMES), fm, as_of="2025-06-01", model_version="v")
    snap = rows[0]["payload"]["feature_snapshot"]
    assert snap[FEATURE_NAMES[0]] is None


def test_score_all_empty() -> None:
    """空入力なら []。"""
    empty = pd.DataFrame(columns=list(FEATURE_NAMES))
    assert (
        score_all(_StubModel([]), list(FEATURE_NAMES), empty, as_of="2025-06-01", model_version="v")
        == []
    )
