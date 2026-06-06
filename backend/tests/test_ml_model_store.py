"""model_store の検証 — 実 tiny .pkl の往復・メタ検証・欠損/不一致（Phase 5・ADR-018）。

担保（phase5-spec.md §4.2/§9）:
- 別 PC 学習相当（合成データの実 LightGBM）を save_model で書き → load_active が model+meta を返す。
- 未配置（latest 無し）は is_configured=False・load_active は ModelLoadError。
- feature_names 不一致 → ModelLoadError（静かな事故防止）。lib_version 不一致 → 警告のみ（続行）。
- .pkl 欠損 → ModelLoadError。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import lightgbm
import numpy as np
import pandas as pd
import pytest

from app.ml.model_store import ModelLoadError, is_configured, load_active
from app.quant.ml.features import FEATURE_NAMES
from app.quant.ml.train import save_model, train_model

_STEM = "ai_alpha-2026-06-15"


def _make_model(out_dir: Path) -> pd.DataFrame:
    """合成データで極小 LightGBM を学習し out_dir に保存（実 joblib+lightgbm 往復）。戻り: X。"""
    rng = np.random.default_rng(0)
    x = pd.DataFrame(rng.normal(size=(40, len(FEATURE_NAMES))), columns=list(FEATURE_NAMES))
    y = pd.Series(x.to_numpy() @ rng.normal(size=len(FEATURE_NAMES)))
    model, _ = train_model(x, y, list(FEATURE_NAMES), params={"n_estimators": 10, "num_leaves": 7})
    save_model(
        model,
        list(FEATURE_NAMES),
        out_dir=str(out_dir),
        trained_at="2026-06-15",
        target="excess_return_60d",
        lib_version=lightgbm.__version__,
    )
    return x


def test_load_active_ok(tmp_path: Path) -> None:
    """正常配置 → load_active が (model, meta) を返し predict できる。is_configured=True。"""
    x = _make_model(tmp_path)
    assert is_configured("ai_alpha", model_dir=str(tmp_path)) is True

    model, meta = load_active("ai_alpha", model_dir=str(tmp_path))
    assert meta.model_id == _STEM
    assert meta.feature_names == list(FEATURE_NAMES)
    assert model.predict(x[list(FEATURE_NAMES)]).shape == (40,)


def test_not_configured(tmp_path: Path) -> None:
    """latest が無ければ is_configured=False・load_active は ModelLoadError。"""
    assert is_configured("ai_alpha", model_dir=str(tmp_path)) is False
    with pytest.raises(ModelLoadError):
        load_active("ai_alpha", model_dir=str(tmp_path))


def test_feature_names_mismatch(tmp_path: Path) -> None:
    """メタの feature_names が推論側と違えば ModelLoadError（静かな事故防止）。"""
    _make_model(tmp_path)
    meta_path = tmp_path / f"{_STEM}.json"
    meta = json.loads(meta_path.read_text())
    meta["feature_names"] = ["wrong_a", "wrong_b"]
    meta_path.write_text(json.dumps(meta))
    with pytest.raises(ModelLoadError):
        load_active("ai_alpha", model_dir=str(tmp_path))


def test_lib_version_mismatch_warns(tmp_path: Path, caplog) -> None:
    """lib_version 不一致は警告のみでロードは成功する。"""
    _make_model(tmp_path)
    meta_path = tmp_path / f"{_STEM}.json"
    meta = json.loads(meta_path.read_text())
    meta["lib_version"] = "0.0.0-test"
    meta_path.write_text(json.dumps(meta))
    with caplog.at_level(logging.WARNING):
        model, _ = load_active("ai_alpha", model_dir=str(tmp_path))
    assert model is not None
    assert any("バージョン不一致" in r.message for r in caplog.records)


def test_missing_pkl(tmp_path: Path) -> None:
    """latest が指す .pkl が無ければ ModelLoadError。"""
    _make_model(tmp_path)
    (tmp_path / f"{_STEM}.pkl").unlink()
    with pytest.raises(ModelLoadError):
        load_active("ai_alpha", model_dir=str(tmp_path))
