"""学習済みモデルの読込・検証（Phase 5・ADR-006/016/018）。

設計の真実: docs/phase-specs/phase5-spec.md §4.2。

- **ファイル I/O のみ・DB を知らない**。`backend/models/` の `.pkl`＋メタ JSON を検証して返す。
- バージョニング: ファイル名の学習日（`ai_alpha-<日付>.pkl`）＋ `ai_alpha-latest.json` が現用。
  ロールバックは latest の `active` を旧 stem に書き換えるだけ（§4.2）。
- **学習時と推論時の特徴量の不一致は静かな事故**（ADR-018）。`feature_names` を推論側の
  `FEATURE_NAMES` と照合し不一致なら `ModelLoadError`。`lib_version` 不一致は警告のみ。
- **pickle のセキュリティ**: `.pkl` は**利用者が別 PC で学習し rsync する自作物**（ADR-001 単一
  ユーザー・ADR-006）で信頼できる供給元。外部由来の pickle は読まない前提なので joblib.load を使う。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import settings
from app.quant.ml.features import FEATURE_NAMES

logger = logging.getLogger(__name__)


class ModelLoadError(RuntimeError):
    """モデルの読込・検証に失敗（pkl/メタ欠損・feature_names 不一致等）。ジョブが翻訳する。"""


@dataclass(frozen=True)
class ModelMeta:
    """モデルの併置メタ（§4.2）。学習時に save_model が JSON で書く。"""

    model_id: str
    trained_at: str
    feature_names: list[str]
    lib_version: str
    target: str
    notes: str


def _resolve_dir(model_dir: str | None) -> Path:
    return Path(model_dir if model_dir is not None else settings.ml_model_dir)


def _latest_path(kind: str, model_dir: str | None) -> Path:
    return _resolve_dir(model_dir) / f"{kind}-latest.json"


def is_configured(kind: str = "ai_alpha", *, model_dir: str | None = None) -> bool:
    """現用ポインタ（`<kind>-latest.json`）が存在するか。

    未配置（まだ別 PC で学習していない・rsync していない）なら False。ジョブはこの場合
    「失敗」ではなく「未配置 skip」として静かに飛ばす（本番の毎晩バッチを誤通知で鳴らさない）。
    """
    return _latest_path(kind, model_dir).is_file()


def load_active(kind: str = "ai_alpha", *, model_dir: str | None = None) -> tuple[Any, ModelMeta]:
    """現用モデルと検証済みメタを返す（§4.2）。問題があれば `ModelLoadError`。

    手順: `<kind>-latest.json` の `active` → 対応 `.pkl` を joblib.load・併置メタ JSON を読む。
    検証: latest/.pkl/メタ欠損 → ModelLoadError、`feature_names != FEATURE_NAMES` → ModelLoadError、
    `lib_version != lightgbm.__version__` → 警告のみ（ロードは続行）。
    """
    import joblib

    base = _resolve_dir(model_dir)
    latest = base / f"{kind}-latest.json"
    if not latest.is_file():
        raise ModelLoadError(f"現用ポインタが無い: {latest}")

    try:
        active = json.loads(latest.read_text(encoding="utf-8")).get("active")
    except (OSError, json.JSONDecodeError) as exc:
        raise ModelLoadError(f"latest の読込に失敗: {latest}: {exc}") from exc
    if not active:
        raise ModelLoadError(f"latest に active が無い: {latest}")

    pkl_path = base / f"{active}.pkl"
    meta_path = base / f"{active}.json"
    if not pkl_path.is_file():
        raise ModelLoadError(f"モデル .pkl が無い: {pkl_path}")
    if not meta_path.is_file():
        raise ModelLoadError(f"メタ JSON が無い: {meta_path}")

    try:
        meta_raw = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ModelLoadError(f"メタ JSON の読込に失敗: {meta_path}: {exc}") from exc

    try:
        meta = ModelMeta(
            model_id=str(meta_raw["model_id"]),
            trained_at=str(meta_raw["trained_at"]),
            feature_names=list(meta_raw["feature_names"]),
            lib_version=str(meta_raw["lib_version"]),
            target=str(meta_raw["target"]),
            notes=str(meta_raw.get("notes", "")),
        )
    except (KeyError, TypeError) as exc:
        raise ModelLoadError(f"メタ JSON が不正: {meta_path}: {exc}") from exc

    # 特徴量定義の不一致は静かな事故（ADR-018）。推論側の FEATURE_NAMES と厳密一致を要求。
    if meta.feature_names != list(FEATURE_NAMES):
        raise ModelLoadError(
            f"feature_names が不一致: {meta.feature_names} != {list(FEATURE_NAMES)}"
        )

    # 信頼できる自作 .pkl のみ読む（モジュール docstring のセキュリティ注記）。
    try:
        model = joblib.load(pkl_path)
    except Exception as exc:  # noqa: BLE001 — 破損 pkl 等を ModelLoadError に翻訳
        raise ModelLoadError(f"モデル .pkl の読込に失敗: {pkl_path}: {exc}") from exc

    _warn_on_lib_mismatch(meta.lib_version)
    return model, meta


def _warn_on_lib_mismatch(meta_version: str) -> None:
    """学習時と推論時の lightgbm バージョン不一致を警告（拒否はしない・§4.2）。"""
    try:
        import lightgbm

        if meta_version and meta_version != lightgbm.__version__:
            logger.warning(
                "lightgbm バージョン不一致: モデル学習時=%s / 推論時=%s（推論は続行）",
                meta_version,
                lightgbm.__version__,
            )
    except Exception:  # noqa: BLE001 — 警告のための照合なので失敗しても推論は止めない
        logger.debug("lightgbm バージョン照合をスキップ")
