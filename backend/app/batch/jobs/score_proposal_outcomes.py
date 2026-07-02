"""AI 過去提案の市場結果採点ジョブ（夜バッチ初の backward-looking・ADR-077・テーマ A）。

設計の真実: docs/decisions.md ADR-077・tasks/hermes-transfer-2026-07-02.md。

夜の分析AI・チャットが出した buy/sell 提案（proposals・ADR-052）と注目選別（notable_picks・
ADR-067）を、提案日終値を起点に N 営業日後の実現（超過）リターンで事後採点し proposal_outcomes を
冪等 UPSERT する（services/track_record.score_pending_outcomes）。夜バッチで唯一「昨日を振り返る」
ジョブ。fetch_quotes/fetch_index/fetch_us_quotes で当日終値＋ベンチが揃い、run_advisor で当夜提案が
永続した後に置く（NIGHTLY_JOBS で notify_digest の直前）。

冪等（ADR-002）: UPSERT。horizon 未経過は pending で保留し、データが追いついた夜に final へ上書き
される（Free/Light の鮮度遅延を系列カウントが自然に吸収）。例外はジョブ境界で握り後続を止めない
（ADR-018）。W2 境界（begin）を本ジョブが所有する（services は commit しない）。
"""

from __future__ import annotations

import logging

from app.batch.runner import JobResult
from app.db.engine import get_engine
from app.services.track_record import score_pending_outcomes

logger = logging.getLogger(__name__)


def run() -> JobResult:
    """過去 buy/sell 提案＋notable を市場結果で採点し proposal_outcomes を UPSERT（ADR-077）。"""
    name = "score_proposal_outcomes"
    try:
        with get_engine().begin() as conn:  # W2 境界をジョブが所有（複数行 atomic・冪等 UPSERT）
            counts = score_pending_outcomes(conn)
    except Exception as exc:  # noqa: BLE001 — ジョブ境界で握り runner に返す
        logger.exception("score_proposal_outcomes が失敗")
        return JobResult(name=name, ok=False, rows=0, detail=f"失敗: {exc}")

    return JobResult(
        name=name,
        ok=True,
        rows=counts["upserted"],
        detail=f"採点 {counts['upserted']} 行 UPSERT（うち final {counts['finalized']}）",
    )
