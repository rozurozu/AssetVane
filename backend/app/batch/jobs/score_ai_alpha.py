"""AI Alpha Scorer 推論ジョブ — 学習済みモデルで全銘柄をスコアリング（Phase 5・ADR-006/018）。

設計の真実: docs/phase-specs/phase5-spec.md §4.4。

data-arch×quant の境界（糊）。quant の純関数（features / infer）は DB を知らない（ADR-016）。この
ジョブが repo から financials/日足を読んで特徴量を point-in-time で組み、`model_store.load_active`
で読んだモデルで `score_all` し signal_type='ai_alpha' で冪等 UPSERT（payload は json.dumps）。

**モデル未配置時の挙動（本番安全側・ADR-018 の意図を踏襲）**:
- 未配置（`<kind>-latest.json` 無し＝まだ別 PC で学習・rsync していない）→ **ok=True で静かに skip**
  （通知しない）。Phase 1〜6 が本番稼働中に本ジョブを追加しても、毎晩の偽アラートを鳴らさないため。
- 配置済みだが読込失敗（pkl/メタ欠損・feature_names 不一致）→ **ok=False**（runner が通知）。
  これは「本物の失敗」なので大きく鳴らす。いずれも前日 signals は残る（ADR-018）。
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pandas as pd

from app.batch.runner import JobResult
from app.db import repo
from app.db.engine import get_engine
from app.ml import model_store
from app.quant.ml.features import build_features_at
from app.quant.ml.infer import score_all

logger = logging.getLogger(__name__)

_FINANCIALS_LOOKBACK = 12  # YoY 突合に足る直近開示件数（約 3 年・Free は約 2 年）


def _prices_df(quotes: list[dict[str, Any]]) -> pd.DataFrame:
    """get_quotes の dict 列から date 昇順の [date, adj_close] DataFrame を作る。"""
    return pd.DataFrame(quotes, columns=["date", "adj_close"])


def run() -> JobResult:
    """学習済み ai_alpha モデルで全銘柄をスコアリングし signals を冪等 UPSERT する（§4.4）。

    モデル未配置は ok=True で静かに skip、読込失敗は ok=False（runner が通知）。それ以外の例外も
    握って ok=False で返す（後続ジョブを止めない＝ADR-018）。
    """
    name = "score_ai_alpha"
    feats_by_code: dict[str, dict[str, float]] = {}
    try:
        if not model_store.is_configured("ai_alpha"):
            logger.info("ai_alpha モデル未配置。スコアリングを skip する。")
            return JobResult(name=name, ok=True, rows=0, detail="モデル未配置のため skip")

        try:
            model, meta = model_store.load_active("ai_alpha")
        except model_store.ModelLoadError as exc:
            logger.error("ai_alpha モデル読込失敗: %s", exc)
            return JobResult(name=name, ok=False, rows=0, detail=f"モデル読込失敗: {exc}")

        with get_engine().connect() as conn:
            as_of = repo.get_max_quote_date(conn)
            if as_of is None:
                return JobResult(name=name, ok=True, rows=0, detail="日足が無い（skip）")
            for code in repo.list_stock_codes(conn):
                quotes = repo.get_quotes(conn, code)
                if not quotes:
                    continue
                fin = repo.get_financials(conn, code, limit=_FINANCIALS_LOOKBACK)
                feats = build_features_at(fin, _prices_df(quotes), as_of=as_of)
                if feats is not None:
                    feats_by_code[code] = feats

        if not feats_by_code:
            return JobResult(name=name, ok=True, rows=0, detail="特徴量を組める銘柄が無い（skip）")

        # from_dict の列は FEATURE_NAMES 順。列選択は score_all 側で行う。
        matrix = pd.DataFrame.from_dict(feats_by_code, orient="index")
        rows = score_all(model, meta.feature_names, matrix, as_of, meta.model_id)
        out_rows = [
            {
                "date": r["date"],
                "code": r["code"],
                "signal_type": r["signal_type"],
                "score": r["score"],
                # payload は json.dumps 済み文字列で渡す契約（repo は変換しない）。
                "payload": json.dumps(r["payload"], ensure_ascii=False),
            }
            for r in rows
        ]
        n = repo.upsert_signals(out_rows)
    except Exception as exc:  # noqa: BLE001 — ジョブ境界で握り runner に返す
        logger.exception("score_ai_alpha が失敗")
        return JobResult(name=name, ok=False, rows=0, detail=f"失敗: {exc}")

    return JobResult(
        name=name,
        ok=True,
        rows=n,
        detail=f"{len(feats_by_code)} 銘柄を ai_alpha スコアリング・signals {n} 行 UPSERT",
    )
