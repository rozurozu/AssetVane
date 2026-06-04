"""バリュエーション計算ジョブ — 全銘柄の PER/PBR/時価総額/配当利回りを焼く（ADR-031）。

services.valuation が「最新FY実績 EPS/BPS × 最新開示の配当/株数 × 最新終値」を畳んで
valuation_snapshots 行を組み立て、このジョブが冪等 UPSERT する（計算は Python・ADR-014/016）。
fetch_financials（財務）と fetch_quotes（株価）の後に回す前提（NIGHTLY_JOBS の順序）。
例外は握って JobResult(ok=False) で返す（runner が Discord 通知・ADR-018）。
"""

from __future__ import annotations

import logging

from app.batch.runner import JobResult
from app.db import repo
from app.db.engine import get_engine
from app.services import valuation as valsvc

logger = logging.getLogger(__name__)


def run() -> JobResult:
    """全銘柄の valuation_snapshots を組み立てて冪等 UPSERT する（ADR-031）。"""
    try:
        with get_engine().connect() as conn:
            rows = valsvc.build_valuation_snapshots(conn)
        n = repo.upsert_valuation_snapshots(rows)
    except Exception as exc:  # noqa: BLE001 — ジョブ境界で握り runner に返す
        logger.exception("calc_valuation が失敗")
        return JobResult(name="calc_valuation", ok=False, rows=0, detail=f"失敗: {exc}")

    return JobResult(
        name="calc_valuation",
        ok=True,
        rows=n,
        detail=f"valuation_snapshots {n} 行 UPSERT",
    )
