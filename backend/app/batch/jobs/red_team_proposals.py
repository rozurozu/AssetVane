"""夜間バッチ: 提案前 red-team 反証ジョブ（skeptic 面・ADR-086・提案の自己反証）。

設計の真実: docs/decisions.md ADR-086。

当夜 pending の buy/sell 提案（run_advisor が生成／昼 chat が起票）を、生成面とは別の skeptic 面で
反証し body.skeptic に注記する（advisor/skeptic.py が本体）。NIGHTLY_JOBS では投資家プロファイル
蒸留（distill_investor_profile）の後・通知系（notify_cost_warn/notify_digest）の前に置く＝当夜の
ニュース polarity（tag_news_polarity）やドシエ（investigate_dossier）が enrich された後に反証させ、
かつ digest が反証件数を 1 行で出せるようにする。

障害設計（ADR-018）:
- skeptic 面 **未設定** = FaceNotConfiguredError を沈黙 skip（ok=True・reviewer/profiler 同型）。
  反証ジョブは日次運用に非 critical なので、未設定を runner 集約通知で nag しない。
- ゲート skip（未反証の pending 提案が 0 件）も ok=True（健全な no-op）。
- LLM **ハード失敗**（設定済みで落ちる）= 実障害なので ok=False で surface（runner が集約通知）。

W2 境界（begin）を本ジョブが所有し、asyncio.run で反証を駆動する（distill_experience と同型）。
"""

from __future__ import annotations

import asyncio
import logging

from app.advisor.skeptic import run_skeptic_review
from app.batch.runner import JobResult
from app.db.engine import get_engine
from app.services.llm_config import FaceNotConfiguredError

logger = logging.getLogger(__name__)


def run() -> JobResult:
    """当夜 pending の buy/sell 提案を反証し body.skeptic に注記する（ADR-086）。

    body への反証注記を 1 トランザクションで原子化するため begin() で包む。未設定/ゲート skip は
    ok=True、LLM ハード失敗は ok=False（ADR-018）。
    """
    name = "red_team_proposals"
    try:
        with get_engine().begin() as conn:
            result = asyncio.run(run_skeptic_review(conn))
    except FaceNotConfiguredError:
        # skeptic 面 未設定＝沈黙 skip（nag しない・ADR-018/058）。
        return JobResult(name=name, ok=True, rows=0, detail="skeptic 面 未設定でskip")
    except Exception as exc:  # noqa: BLE001 — ハード失敗。runner が集約通知する（ADR-018）
        logger.exception("red_team_proposals が失敗")
        return JobResult(name=name, ok=False, rows=0, detail=f"失敗: {exc}")

    reviewed = result.get("reviewed")
    rows = len(reviewed) if isinstance(reviewed, list) else 0
    return JobResult(name=name, ok=True, rows=rows, detail=str(result.get("reason") or ""))
