"""夜間バッチ: 日米業種リードラグ・シグナル計算ジョブ（Phase 7・SIG-FIN-036-13・batch-pattern）。

設計の真実: 論文 SIG-FIN-036-13・ADR-014/016（事実は Python が計算）・ADR-002（冪等 UPSERT）・
ADR-018（部分失敗から再開・縮退は失敗にしない）。

NIGHTLY_JOBS の `calc_signals.run` の後・`run_advisor.run` の前で呼ばれる（当日の US/JP 業種 ETF
の事実が揃ってから算出し、夜の分析AI が当日の lead_lag を読めるようにする）。services.lead_lag
が rcc/roc 整合〜quant 呼び出し〜行組み立てを担い、本ジョブは糊として
build_lead_lag_signals → upsert_signals（payload を json.dumps して repo へ渡す契約）。

データ不足・窓縮退（service が rows=0 を返す）は ok=True/rows=0＋detail に理由（縮退は失敗に
しない＝ADR-018）。例外はジョブ境界で握り JobResult(ok=False) を返す（後続ジョブを止めない）。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.batch.runner import JobResult
from app.db import repo
from app.db.engine import get_engine
from app.services.lead_lag import build_lead_lag_signals

logger = logging.getLogger(__name__)


def run() -> JobResult:
    """日米業種リードラグ・シグナルを計算し signals を冪等 UPSERT する（Phase 7）。

    縮退（データ不足・窓不足）は失敗にせず ok=True/rows=0 を返す（ADR-018）。
    例外は握って JobResult(ok=False) で返す（runner が Discord 通知）。
    """
    try:
        with get_engine().connect() as conn:
            rows, meta = build_lead_lag_signals(conn)

        if not rows:
            reason = meta.get("reason", "no_signal")
            return JobResult(
                name="calc_lead_lag",
                ok=True,
                rows=0,
                detail=f"シグナル生成なし（{reason}・縮退は失敗にしない）",
            )

        # payload（dict）を json.dumps 済み文字列にして repo へ（calc_signals と同じ契約）。
        out_rows: list[dict[str, Any]] = [
            {**r, "payload": json.dumps(r["payload"], ensure_ascii=False)} for r in rows
        ]
        n = repo.upsert_signals(out_rows)
    except Exception as exc:  # noqa: BLE001 — ジョブ境界で握り runner に返す
        logger.exception("calc_lead_lag が失敗")
        return JobResult(name="calc_lead_lag", ok=False, rows=0, detail=f"失敗: {exc}")

    return JobResult(
        name="calc_lead_lag",
        ok=True,
        rows=n,
        detail=f"lead_lag {n} 業種を UPSERT（as_of={meta.get('as_of')}・ic={meta.get('ic')}）",
    )
