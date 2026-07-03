"""夜間バッチ: 投資家プロファイル蒸留ジョブ（profiler 面・ADR-082・テーマ C・ループ ④）。

設計の真実: docs/decisions.md ADR-082・tasks/hermes-transfer-2026-07-02.md テーマ C。

取引台帳の行動信号（services/investor_behavior）を教材に、profiler 面の Tool ループで投資家
プロファイルの傾向メモ draft を蒸留する（advisor/profiler.py が本体）。NIGHTLY_JOBS では
distill_experience（ADR-081）の後・通知系の前に置く（backward-looking な蒸留ペアを並べる）。

障害設計（ADR-018・distill_experience と同型）:
- profiler 面 **未設定** = FaceNotConfiguredError を沈黙 skip（ok=True・reviewer/tagger 同型）。
- 活動量ゲート skip（新規 SELL < 閾値）も ok=True（健全な no-op）。
- LLM **ハード失敗**（設定済みで落ちる）= 実障害なので ok=False で surface（runner が集約通知）。
- skip/失敗時はカーソルを前進させない（profiler 側が成功時のみ前進・材料を失わない）。

W2 境界（begin）を本ジョブが所有し、asyncio.run で蒸留を駆動する（distill_experience 同型）。
"""

from __future__ import annotations

import asyncio
import logging

from app.advisor.profiler import run_profile_distillation
from app.batch.runner import JobResult
from app.db.engine import get_engine
from app.services.llm_config import FaceNotConfiguredError

logger = logging.getLogger(__name__)


def run() -> JobResult:
    """台帳の行動信号を投資家プロファイルの傾向メモ draft に蒸留する（ADR-082）。

    書き込み（profile_note 起票＋カーソル前進）を 1 トランザクションで原子化するため begin() で
    包む。未設定/ゲート skip は ok=True、LLM ハード失敗は ok=False（ADR-018）。
    """
    name = "distill_investor_profile"
    try:
        with get_engine().begin() as conn:
            result = asyncio.run(run_profile_distillation(conn))
    except FaceNotConfiguredError:
        # profiler 面 未設定＝沈黙 skip（nag しない・ADR-018/058）。
        return JobResult(name=name, ok=True, rows=0, detail="profiler 面 未設定でskip")
    except Exception as exc:  # noqa: BLE001 — ハード失敗。runner が集約通知する（ADR-018）
        logger.exception("distill_investor_profile が失敗")
        return JobResult(name=name, ok=False, rows=0, detail=f"失敗: {exc}")

    notes = result.get("notes")
    rows = len(notes) if isinstance(notes, list) else 0
    return JobResult(name=name, ok=True, rows=rows, detail=str(result.get("reason") or ""))
