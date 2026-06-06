"""AI Alpha Scorer の推論 — 全銘柄の予測 → 当日内ランク正規化（Phase 5・ADR-014/016）。

設計の真実: docs/phase-specs/phase5-spec.md §4.3。

- **DB を知らない純関数**（ADR-016）。引数はモデルと特徴量、戻り値は signals 行候補 dict の配列。
  DB 書き込みも json.dumps もジョブ側（score_ai_alpha・calc_signals の契約に揃える）。
- **score = 予測超過リターンの当日内パーセンタイル順位（0..1）**。生予測値は payload に保持。
- **決定性**（同じ .pkl＋特徴量→同じ score＝ADR-016）。`rank(pct=True)` は決定的（同点=平均順位）。
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

_SCHEMA_VERSION = 1  # payload スキーマ版（features._SCHEMA_VERSION と整合）
_LABEL = "AI 決算スコア"  # 一覧 UI の人間可読ラベル（momentum/volume_spike と一貫・§5）


def _jsonable(value: float) -> float | None:
    """NaN/inf は JSON 不正（JS が parse 不可）なので None に倒す（payload 用・ADR-014）。"""
    return float(value) if np.isfinite(value) else None


def score_all(
    model: Any,
    feature_names: list[str],
    feature_matrix: pd.DataFrame,
    as_of: str,
    model_version: str,
) -> list[dict[str, Any]]:
    """全銘柄の特徴量行列から signals 行候補（ai_alpha）の配列を返す（§4.3・DB は書かない）。

    feature_matrix: index=code・columns⊇feature_names（point-in-time で as_of に組んだ特徴量）。
    手順: 1) model.predict（列順を feature_names に強制）、2) 当日内パーセンタイル順位で score、
    3) {date, code, signal_type:'ai_alpha', score, payload(dict)} を返す。空入力なら []。
    """
    if feature_matrix is None or feature_matrix.empty:
        return []

    x = feature_matrix[feature_names]
    preds = np.asarray(model.predict(x), dtype=float)
    codes = [str(c) for c in feature_matrix.index]

    # 当日内パーセンタイル順位（0..1）。同点は平均順位＝決定的（ADR-016）。
    scores = pd.Series(preds, index=range(len(preds))).rank(pct=True)

    rows: list[dict[str, Any]] = []
    for i, code in enumerate(codes):
        snapshot = {name: _jsonable(float(x.iloc[i][name])) for name in feature_names}
        payload: dict[str, Any] = {
            "predicted_excess_return_60d": _jsonable(float(preds[i])),
            "model_version": model_version,
            "feature_snapshot": snapshot,
            "label": _LABEL,
            "schema_version": _SCHEMA_VERSION,
        }
        rows.append(
            {
                "date": as_of,
                "code": code,
                "signal_type": "ai_alpha",
                "score": float(scores.iloc[i]),
                "payload": payload,
            }
        )
    return rows
