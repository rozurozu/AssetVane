"""夜間バッチ: 軸1 夜の分析AI ジョブ（spec §5・ADR-011/018）。

NIGHTLY_JOBS の末尾（事実が揃ってから）で呼ばれる。同期ジョブとして JobResult を返す
（runner.py の規律）。内部で `with get_engine().begin() as conn:` を開き、
`asyncio.run(run_nightly_advisor(conn))` で非同期の分析を駆動する（同期バッチから async を回す）。

失敗（LLM/DB 等の例外）・無応答（observations 空の縮退）はいずれも JobResult(ok=False)＋detail
で返す（後続ジョブを止めない・夜の分析失敗が data を壊さない）。Discord 通知は runner 集約が担う
（ジョブ自身は notify しない・ADR-007/018）。
"""

from __future__ import annotations

import asyncio
import logging

from app.advisor.nightly import run_nightly_advisor
from app.batch.runner import JobResult
from app.db.engine import get_engine

logger = logging.getLogger(__name__)


def run() -> JobResult:
    """夜の分析AI を駆動し advisor_journal を 1 件生成する（spec §5・ADR-018）。

    書き込み（journal/proposal 起票）を 1 トランザクションで原子化するため begin() で包む。
    run_nightly_advisor は ②ハード失敗（LLM/DB 等）を例外で伝播し、③縮退（observations 空）を
    理由文字列で返す。本ジョブはいずれも ok=False＋detail に畳む（Discord 通知は runner 集約）。
    """
    try:
        with get_engine().begin() as conn:
            reason = asyncio.run(run_nightly_advisor(conn))
    except Exception as exc:  # noqa: BLE001 — ②ハード失敗（LLM/DB 等）。runner が集約通知する
        logger.exception("run_advisor: 失敗")
        return JobResult(name="run_advisor", ok=False, rows=0, detail=f"夜AI 実行失敗: {exc}")
    if reason:  # ③縮退（無応答）
        return JobResult(name="run_advisor", ok=False, rows=0, detail=reason)
    return JobResult(name="run_advisor", ok=True, rows=1, detail="夜の分析を実行")
