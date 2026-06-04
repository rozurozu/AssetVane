"""夜間バッチ: 軸1 夜の分析AI ジョブ（spec §5・ADR-011/018）。

NIGHTLY_JOBS の末尾（事実が揃ってから）で呼ばれる。同期ジョブとして JobResult を返す
（runner.py の規律）。内部で `with get_engine().begin() as conn:` を開き、
`asyncio.run(run_nightly_advisor(conn))` で非同期の分析を駆動する（同期バッチから async を回す）。

例外は握って JobResult(ok=False) で返す（後続ジョブを止めない・夜の分析失敗が data を壊さない）。
LLM 失敗時の Discord 通知・journal スキップは run_nightly_advisor 側が担う（ADR-018）。
"""

from __future__ import annotations

import asyncio
import logging

from app.advisor.nightly import run_nightly_advisor
from app.batch.runner import JobResult
from app.db.engine import get_engine

logger = logging.getLogger(__name__)


def run() -> JobResult:
    """夜の分析AI を駆動し advisor_journal を 1 件生成する（spec §5）。

    書き込み（journal/proposal 起票）を 1 トランザクションで原子化するため begin() で包む。
    run_nightly_advisor は LLM 失敗時に内部で journal をスキップ＋通知して握るため、
    ここまで例外が来るのは想定外（DB 等）のケース → JobResult(ok=False) で返す。
    """
    try:
        with get_engine().begin() as conn:
            asyncio.run(run_nightly_advisor(conn))
        return JobResult(name="run_advisor", ok=True, rows=1, detail="夜の分析を実行")
    except Exception as exc:  # noqa: BLE001 — ジョブ境界で握り runner に返す
        logger.exception("run_advisor: 失敗")
        return JobResult(name="run_advisor", ok=False, rows=0, detail=str(exc))
