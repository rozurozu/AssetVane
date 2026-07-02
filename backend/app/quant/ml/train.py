"""AI Alpha Scorer の学習 — 別 PC でのみ実行（ADR-006）。ラズパイ cron からは呼ばれない。

設計の真実: docs/phase-specs/phase5-spec.md §3／docs/ml-training.md（再現手順）。

- **学習は別 PC**（ADR-006）。本モジュールはリポジトリに置くが、夜間バッチ（ラズパイ）の
  `NIGHTLY_JOBS` には含めない。出力 `.pkl`＋メタを `backend/models/` へ rsync して推論に使う。
- **特徴量は features.py を再利用**（学習・推論で同一定義＝再現性・ADR-016）。リーク防止は
  features の point-in-time に加え、ラベル窓（未来）が特徴量窓（過去）と重ならないことを assert。
- **ラベル（既定・U-4 裁定済み）**: 決算開示後 60 営業日の対 TOPIX 超過リターンを**回帰**で予測。
  `label_kind`/`label_horizon_days` で差替可（分類化はしない方針だが分岐は残す）。
- **数字を作らない**（ADR-014）: 特徴量・ラベルが組めない (code, 開示) はサンプルから除外（skip）。
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Literal, cast, overload

import numpy as np
import pandas as pd

from app.quant.ml.features import FEATURE_NAMES, build_features_at


# with_dates で戻りタプルの要素数が変わるため overload で呼び出し側を型安全にする（既定=3 タプル・
# True=4 タプル）。全呼び出しがリテラル/省略なので pyright が正しい分岐を選べる。
@overload
def build_training_set(
    financials: pd.DataFrame,
    prices: pd.DataFrame,
    benchmark: pd.Series,
    *,
    label_horizon_days: int = ...,
    label_kind: str = ...,
    with_dates: Literal[False] = ...,
) -> tuple[pd.DataFrame, pd.Series, list[str]]: ...


@overload
def build_training_set(
    financials: pd.DataFrame,
    prices: pd.DataFrame,
    benchmark: pd.Series,
    *,
    label_horizon_days: int = ...,
    label_kind: str = ...,
    with_dates: Literal[True],
) -> tuple[pd.DataFrame, pd.Series, list[str], pd.Series]: ...


def build_training_set(
    financials: pd.DataFrame,
    prices: pd.DataFrame,
    benchmark: pd.Series,
    *,
    label_horizon_days: int = 60,
    label_kind: str = "regression",
    with_dates: bool = False,
) -> (
    tuple[pd.DataFrame, pd.Series, list[str]] | tuple[pd.DataFrame, pd.Series, list[str], pd.Series]
):
    """point-in-time で特徴量 X・ラベル y・feature_names を組む純関数（リーク防止＝§2/§3）。

    引数:
      financials: columns=[code, disclosed_date, fiscal_period, net_sales, operating_profit,
        profit, eps, bps]。
      prices: columns=[code, date, adj_close]（date 昇順想定・内部で銘柄ごとに整列）。
      benchmark: TOPIX 終値（index=date 文字列・value=close）。対ベンチ超過リターン算出用。
      label_horizon_days: ラベルの保有営業日数（既定 60）。
      label_kind: 'regression'（既定・超過リターンそのもの）/ 'classification'（符号で 2 値化）。
      with_dates: True なら 4 つ目に各サンプルの as_of（開示日）Series を返す。walk-forward CV で
        時系列順にソートして fold を切るために使う（既定 False＝後方互換の 3 タプル）。

    各 (code, disclosed_date) を as_of として 1 サンプルを作る:
      X 行 = features.build_features_at(...)、
      y    = (銘柄 adj_close[t+H]/adj_close[t] - 1) - (TOPIX[t+H]/TOPIX[t] - 1)。
      t = 開示日の**翌**営業日（観測→翌日エントリ）。特徴量窓（<= 開示日）とラベル窓（t..t+H）が
      重ならないことを assert（リーク検出）。特徴量・ラベルが組めないサンプルは除外。
    """
    bench = {str(d): float(v) for d, v in benchmark.items() if pd.notna(v)}
    x_rows: list[dict[str, float]] = []
    y_vals: list[float] = []
    as_of_dates: list[str] = []  # 各サンプルの開示日（with_dates 用・CV の時系列分割キー）

    for code, fin_group in financials.groupby("code"):
        fin_df = pd.DataFrame(fin_group)
        fin_rows = cast("list[dict[str, Any]]", fin_df.to_dict("records"))
        pg = pd.DataFrame(prices[prices["code"] == code]).sort_values("date").reset_index(drop=True)
        if pg.empty:
            continue
        p_dates: list[str] = [str(d) for d in pg["date"]]
        adj_series = cast("pd.Series", pd.to_numeric(pd.Series(pg["adj_close"]), errors="coerce"))
        p_adj: list[float] = [float(v) for v in adj_series.to_numpy()]
        price_df = pd.DataFrame({"date": pg["date"], "adj_close": pg["adj_close"]})

        for disclosed_date in sorted({str(d) for d in fin_group["disclosed_date"] if d}):
            feats = build_features_at(fin_rows, price_df, as_of=disclosed_date)
            if feats is None:
                continue
            # t = 開示日の翌営業日（最初の date > disclosed_date）。
            fwd_idx = [i for i, d in enumerate(p_dates) if d > disclosed_date]
            if len(fwd_idx) <= label_horizon_days:
                continue  # t+H まで価格が無い（直近の開示はラベル化できない＝未来リーク防止と整合）
            t_i = fwd_idx[0]
            th_i = t_i + label_horizon_days
            t_date, th_date = p_dates[t_i], p_dates[th_i]
            # リーク防止: ラベル窓の起点はあくまで特徴量カットオフ（as_of）より未来。
            assert t_date > disclosed_date, "ラベル窓が特徴量窓と重複している（リーク）"
            if t_date not in bench or th_date not in bench:
                continue
            p_t, p_th = p_adj[t_i], p_adj[th_i]
            if not (np.isfinite(p_t) and np.isfinite(p_th)) or p_t <= 0:
                continue
            b_t, b_th = bench[t_date], bench[th_date]
            if b_t <= 0:
                continue
            excess = (p_th / p_t - 1.0) - (b_th / b_t - 1.0)
            x_rows.append(feats)
            y_vals.append(excess)
            as_of_dates.append(disclosed_date)

    feature_names = list(FEATURE_NAMES)
    x = pd.DataFrame(x_rows, columns=feature_names)
    y_series = pd.Series(y_vals, dtype=float)
    if label_kind == "classification":
        y_series = (y_series > 0).astype(int)
    if with_dates:
        return x, y_series, feature_names, pd.Series(as_of_dates, dtype="object")
    return x, y_series, feature_names


# LightGBM の既定ハイパラ（docs/ml-training.md に確定値を記録＝再現性・ADR-016）。
_DEFAULT_PARAMS: dict[str, Any] = {
    "n_estimators": 300,
    "learning_rate": 0.03,
    "num_leaves": 31,
    "min_child_samples": 20,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "random_state": 42,
    "n_jobs": 1,
    "verbosity": -1,
}


def train_model(
    x: pd.DataFrame,
    y: pd.Series,
    feature_names: list[str],
    *,
    label_kind: str = "regression",
    params: dict[str, Any] | None = None,
) -> tuple[Any, dict[str, float]]:
    """LightGBM を学習し (model, eval_metrics) を返す（回帰=LGBMRegressor / 分類=LGBMClassifier）。

    評価指標（in-sample・smoke 用。本番の walk-forward CV 値は docs/ml-training.md に記録）:
      回帰 = RMSE / IC（予測と実現超過リターンの spearman 順位相関）、分類 = AUC。
    """
    import lightgbm as lgb

    hp = {**_DEFAULT_PARAMS, **(params or {})}
    x_feat = x[feature_names]
    metrics: dict[str, float] = {}
    if label_kind == "classification":
        model = lgb.LGBMClassifier(**hp)
        model.fit(x_feat, y)
        proba = np.asarray(model.predict_proba(x_feat))[:, 1]
        metrics["auc"] = _auc(y.to_numpy(), proba)
    else:
        model = lgb.LGBMRegressor(**hp)
        model.fit(x_feat, y)
        preds = np.asarray(model.predict(x_feat), dtype=float)
        metrics["rmse"] = float(np.sqrt(np.mean((preds - y.to_numpy()) ** 2)))
        metrics["ic"] = _spearman_ic(preds, y.to_numpy())
    metrics["n_samples"] = float(len(x_feat))
    return model, metrics


def walk_forward_cv(
    x: pd.DataFrame,
    y: pd.Series,
    sample_dates: pd.Series,
    feature_names: list[str],
    *,
    n_splits: int = 5,
    params: dict[str, Any] | None = None,
) -> dict[str, float]:
    """時系列 expanding-window walk-forward CV（リーク防止・docs/ml-training.md §3）。

    サンプルを as_of（sample_dates＝開示日）昇順に並べ、過去ブロックで学習→直後の未来ブロックで
    評価、を n_splits 回繰り返す（expanding window）。各 fold は train の as_of < test の as_of を
    満たし未来を学習に混ぜない（ADR-016 の再現性・リーク防止）。回帰のみ（分類化はしない方針）。
    戻り: fold ごとの RMSE / IC（spearman）の平均・標準偏差・fold 数・サンプル数。サンプルが少なく
    ブロックが作れなければ n_folds=0 で返す（学習側が判断）。
    """
    import lightgbm as lgb

    n = len(x)
    order = np.argsort(sample_dates.to_numpy().astype(str), kind="stable")
    x_sorted = x.iloc[order].reset_index(drop=True)[feature_names]
    y_sorted = y.iloc[order].reset_index(drop=True)
    hp = {**_DEFAULT_PARAMS, **(params or {})}

    # n を (usable+1) ブロックに分け、fold i は [0..b*(i+1)) 学習 / [b*(i+1)..) 評価（expanding）。
    usable = max(1, min(n_splits, n - 1))
    block = n // (usable + 1)
    if block < 1:
        return {"n_samples": float(n), "n_folds": 0.0}

    rmses: list[float] = []
    ics: list[float] = []
    for i in range(usable):
        tr_end = block * (i + 1)
        te_end = block * (i + 2) if i < usable - 1 else n
        x_tr, y_tr = x_sorted.iloc[:tr_end], y_sorted.iloc[:tr_end]
        x_te, y_te = x_sorted.iloc[tr_end:te_end], y_sorted.iloc[tr_end:te_end]
        if len(x_tr) == 0 or len(x_te) == 0:
            continue
        model = lgb.LGBMRegressor(**hp)
        model.fit(x_tr, y_tr)
        preds = np.asarray(model.predict(x_te), dtype=float)
        rmses.append(float(np.sqrt(np.mean((preds - y_te.to_numpy()) ** 2))))
        ics.append(_spearman_ic(preds, y_te.to_numpy()))

    valid_ics = [v for v in ics if math.isfinite(v)]
    return {
        "n_samples": float(n),
        "n_folds": float(len(rmses)),
        "cv_rmse_mean": float(np.mean(rmses)) if rmses else float("nan"),
        "cv_rmse_std": float(np.std(rmses)) if rmses else float("nan"),
        "cv_ic_mean": float(np.mean(valid_ics)) if valid_ics else float("nan"),
        "cv_ic_std": float(np.std(valid_ics)) if valid_ics else float("nan"),
    }


def save_model(
    model: Any,
    feature_names: list[str],
    *,
    out_dir: str,
    kind: str = "ai_alpha",
    trained_at: str,
    target: str,
    lib_version: str,
    notes: str = "",
) -> tuple[str, str]:
    """joblib で `<kind>-<trained_at>.pkl` を保存し、併置メタ JSON と latest ポインタを書く（§4）。

    戻り: (pkl_path, json_path)。latest（`<kind>-latest.json`）は推論側 model_store が現用を引く口。
    """
    import joblib

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{kind}-{trained_at}"
    pkl_path = out / f"{stem}.pkl"
    joblib.dump(model, pkl_path)

    meta = {
        "model_id": stem,
        "trained_at": trained_at,
        "feature_names": feature_names,
        "lib_version": lib_version,
        "target": target,
        "notes": notes,
    }
    json_path = out / f"{stem}.json"
    json_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_path = out / f"{kind}-latest.json"
    latest_path.write_text(json.dumps({"active": stem}, ensure_ascii=False), encoding="utf-8")
    return str(pkl_path), str(json_path)


def _spearman_ic(preds: np.ndarray, y: np.ndarray) -> float:
    """予測と実現値の spearman 順位相関（情報係数 IC）。算出不能は NaN。

    pandas の corr(method="spearman") を使う（scipy より型が素直・戻り値は float）。
    """
    if len(preds) < 2:
        return float("nan")
    rho = float(pd.Series(preds).corr(pd.Series(y), method="spearman"))
    return rho if math.isfinite(rho) else float("nan")


def _auc(y_true: np.ndarray, score: np.ndarray) -> float:
    """二値 AUC（両クラスが揃わないと算出不能 → NaN）。"""
    from sklearn.metrics import roc_auc_score

    if len(set(y_true.tolist())) < 2:
        return float("nan")
    return float(roc_auc_score(y_true, score))
