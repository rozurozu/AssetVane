"""train.py の検証 — build_training_set（純関数・リーク非重複）＋ 学習/保存/復元の smoke。

担保（phase5-spec.md §3・ADR-006/016）:
- build_training_set が (code, 開示日) ごとに X/y を point-in-time で組み、feature_names を返す。
  ラベル窓（未来）と特徴量窓（過去）の非重複 assert が通る（リーク防止）。
- 合成データで LightGBM を学習 → joblib 保存 → 復元 → predict まで通る（fit/save/load smoke）。
学習そのもの（実データ・CV）は別 PC（ADR-006）で CI 対象外。ここは軽量な配管検証のみ。
"""

from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from app.quant.ml.features import FEATURE_NAMES
from app.quant.ml.train import build_training_set, save_model, train_model


def _daily(end: str, periods: int) -> list[str]:
    return list(pd.date_range(end=end, periods=periods, freq="D").strftime("%Y-%m-%d"))


def test_build_training_set_shapes_and_no_leak() -> None:
    """FY 2 期＋価格＋ベンチ → 2 サンプル・列=FEATURE_NAMES・y 有限。リーク assert が通る。"""
    dates = _daily("2024-07-20", 200)
    adj = list(np.linspace(1000.0, 1400.0, 200))
    prices = pd.DataFrame({"code": "7203", "date": dates, "adj_close": adj})
    benchmark = pd.Series(np.linspace(2000.0, 2200.0, 200), index=pd.Index(dates), dtype=float)
    financials = pd.DataFrame(
        [
            {
                "code": "7203",
                "disclosed_date": "2024-02-01",
                "fiscal_period": "FY",
                "net_sales": 1000,
                "operating_profit": 100,
                "profit": 80,
                "eps": 50,
                "bps": 500,
            },
            {
                "code": "7203",
                "disclosed_date": "2024-04-01",
                "fiscal_period": "FY",
                "net_sales": 1200,
                "operating_profit": 150,
                "profit": 96,
                "eps": 60,
                "bps": 550,
            },
        ]
    )

    x, y, names = build_training_set(financials, prices, benchmark, label_horizon_days=60)
    assert names == list(FEATURE_NAMES)
    assert list(x.columns) == list(FEATURE_NAMES)
    assert x.shape == (2, len(FEATURE_NAMES))
    assert len(y) == 2
    assert bool(np.isfinite(y.to_numpy()).all())


def test_train_save_load_smoke(tmp_path: Path) -> None:
    """合成 X/y で回帰学習 → save_model で .pkl/メタ/latest を出力 → joblib 復元 → predict。"""
    rng = np.random.default_rng(0)
    x = pd.DataFrame(rng.normal(size=(60, len(FEATURE_NAMES))), columns=list(FEATURE_NAMES))
    w = rng.normal(size=len(FEATURE_NAMES))
    y = pd.Series(x.to_numpy() @ w + rng.normal(scale=0.1, size=60))

    model, metrics = train_model(
        x, y, list(FEATURE_NAMES), params={"n_estimators": 10, "num_leaves": 7}
    )
    assert "rmse" in metrics and "ic" in metrics and metrics["n_samples"] == 60.0

    pkl_path, json_path = save_model(
        model,
        list(FEATURE_NAMES),
        out_dir=str(tmp_path),
        trained_at="2099-01-01",
        target="excess_return_60d",
        lib_version="test",
    )
    assert Path(pkl_path).exists() and Path(json_path).exists()
    latest = json.loads((tmp_path / "ai_alpha-latest.json").read_text())
    assert latest == {"active": "ai_alpha-2099-01-01"}

    loaded = joblib.load(pkl_path)
    preds = loaded.predict(x[list(FEATURE_NAMES)])
    assert preds.shape == (60,)
