"""夜間バッチ: 経験蒸留ジョブ（reviewer 面・ADR-081・テーマ B・自己改善ループ ④）。

設計の真実: docs/decisions.md ADR-081・tasks/hermes-transfer-2026-07-02.md §8。

score_proposal_outcomes（ADR-077）がその晩の final を採点した直後に走り、採点済み outcome を教材に
reviewer 面の Tool ループで知識カード draft を蒸留する（advisor/reviewer.py が本体）。
NIGHTLY_JOBS で score_proposal_outcomes の後・通知系（notify_cost_warn/notify_digest）の前に置く
（夜バッチ唯一の backward-looking ペア）。

障害設計（ADR-018）:
- reviewer 面 **未設定** = FaceNotConfiguredError を沈黙 skip（ok=True・triage/tagger 同型）。
  学習ジョブは日次運用に非 critical なので、未設定を runner 集約通知で nag しない。
- 活動量ゲート skip（新規 final < 閾値）も ok=True（健全な no-op）。
- LLM **ハード失敗**（設定済みで落ちる）= 実障害なので ok=False で surface（runner が集約通知）。
- skip/失敗時はカーソルを前進させない（reviewer 側が成功時のみ前進・材料を失わない）。

W2 境界（begin）を本ジョブが所有し、asyncio.run で非同期の蒸留を駆動する（run_advisor と同型）。
"""

from __future__ import annotations

import asyncio
import logging

from app.advisor.reviewer import run_experience_distillation
from app.batch.runner import JobResult
from app.db.engine import get_engine
from app.services.llm_config import FaceNotConfiguredError

logger = logging.getLogger(__name__)


def run() -> JobResult:
    """採点済み outcome を知識カード draft に蒸留する（ADR-081）。

    書き込み（card draft/weight 提案の起票＋カーソル前進）を 1 トランザクションで原子化するため
    begin() で包む。未設定/ゲート skip は ok=True、LLM ハード失敗は ok=False（ADR-018）。
    """
    name = "distill_experience"
    try:
        with get_engine().begin() as conn:
            result = asyncio.run(run_experience_distillation(conn))
    except FaceNotConfiguredError:
        # reviewer 面 未設定＝沈黙 skip（nag しない・ADR-018/058）。
        return JobResult(name=name, ok=True, rows=0, detail="reviewer 面 未設定でskip")
    except Exception as exc:  # noqa: BLE001 — ハード失敗。runner が集約通知する（ADR-018）
        logger.exception("distill_experience が失敗")
        return JobResult(name=name, ok=False, rows=0, detail=f"失敗: {exc}")

    drafts = result.get("drafts")
    rows = len(drafts) if isinstance(drafts, list) else 0
    return JobResult(name=name, ok=True, rows=rows, detail=str(result.get("reason") or ""))
