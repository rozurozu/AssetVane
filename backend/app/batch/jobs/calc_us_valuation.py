"""米株バリュエーション計算ジョブ — 全 us_stocks の PER/PBR/時価総額/利回り/ROE/利益率を焼く。

ADR-031/039/048（米市場分離・スクリーナーの土台）・ADR-014/016（計算は Python 純関数）。
日本株 calc_valuation.py をミラーした米株版（既存無改変）。services.us_valuation が「us_stocks の
財務素 × 最新 close」を quant.valuation の純関数で畳んで us_valuation_snapshots 行を組み立て、
このジョブが冪等 UPSERT する。NIGHTLY_JOBS では sync_us_universe → fetch_us_quotes →
fetch_us_fundamentals の後に置く（業種/財務/価格が揃ってから valuation を焼く）。
例外は握って JobResult(ok=False) で返す（runner が Discord 通知・ADR-018）。
"""

from __future__ import annotations

import logging

from app.batch.runner import JobResult
from app.db import repo
from app.db.engine import get_engine
from app.services import us_valuation as us_valsvc

logger = logging.getLogger(__name__)


def run() -> JobResult:
    """全 us_stocks の us_valuation_snapshots を組み立てて冪等 UPSERT する（ADR-031/048）。"""
    try:
        with get_engine().connect() as conn:
            rows = us_valsvc.build_us_valuation_snapshots(conn)
        n = repo.upsert_us_valuation_snapshots(rows)
    except Exception as exc:  # noqa: BLE001 — ジョブ境界で握り runner に返す
        logger.exception("calc_us_valuation が失敗")
        return JobResult(name="calc_us_valuation", ok=False, rows=0, detail=f"失敗: {exc}")

    return JobResult(
        name="calc_us_valuation",
        ok=True,
        rows=n,
        detail=f"us_valuation_snapshots {n} 行 UPSERT",
    )
