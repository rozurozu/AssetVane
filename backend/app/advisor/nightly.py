"""軸1 夜の分析AI（spec §5・ADR-011/014/018/025）。

設計の真実: docs/phase-specs/phase3-spec.md §5。

cron 夜間バッチ（Phase 1 導入）に相乗りし、「昨日までの方針（policy）」と「今日の事実」を
突き合わせて方針見直しを提案し、advisor_journal を 1 件生成して proposal を起票する。
画面コンテキストは無い（ADR-025）。出力は専用 Tool `submit_journal` で受ける（spec §5・決定7）。

障害時（ADR-018）: LLM 失敗（OpenAIError/CostGuardError 等）は complete 側のリトライで吸収し、
最終的に失敗したらその日の journal をスキップして Discord 通知する（例外は握る・signals は前日分
が残る）。conn は呼び出し側（run_advisor ジョブ）が begin() で渡す。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from sqlalchemy import Connection

from app.advisor.prompt_builder import Message, build_messages
from app.advisor.router import _CORE
from app.advisor.service import run_tool_loop
from app.advisor.tools import handlers
from app.advisor.tools.registry import CURRENT_PHASE
from app.advisor.tools.schemas import coerce_policy_change
from app.batch import notify
from app.config import settings
from app.db import repo

logger = logging.getLogger(__name__)

# 夜の定型指示文（spec §5）。画面は無いので「今日の事実を Tool で取り直して突き合わせよ」。
_NIGHTLY_INSTRUCTION = (
    "あなたは夜間の自動分析を担っている。利用可能な Tool（get_signals / get_portfolio_metrics / "
    "get_asset_overview 等）で今日の事実を取り直し、昨日までの方針と突き合わせて、見直しが必要なら"
    "方針変更を提案せよ。最後に必ず submit_journal で所見（observations）・提案（proposal）・"
    "方針変更案（proposed_policy_change）を提出すること。数値は必ず Tool の戻り値のみを使う。"
)


async def _gather_briefing() -> dict[str, object]:
    """事実取得 handler を呼んで briefing dict を組む（_collect_situation_briefing の本体）。

    handlers は内部で読み取り接続を自前で開く。部分失敗しても全体を落とさず、取れた事実だけ
    詰める（handler は例外時 {"error": ...} を返す）。
    """
    signals = await handlers.handle_get_signals({})
    metrics = await handlers.handle_get_portfolio_metrics({})
    overview = await handlers.handle_get_asset_overview({})
    return {"signals": signals, "portfolio_metrics": metrics, "asset_overview": overview}


def collect_situation_briefing(conn: Connection) -> dict[str, object]:
    """今日の signals/portfolio/資産を dict に集約する（監査用・同期入口・spec §5）。

    Tool と同じ事実取得関数（handlers）を呼んで dict にまとめ、
    advisor_journal.situation_briefing に JSON で保存する（「何を見て判断したか」の監査）。
    非同期コンテキスト内からは `_gather_briefing()` を直接 await すること（二重 run 回避）。
    """
    import asyncio

    return asyncio.run(_gather_briefing())


async def run_nightly_advisor(conn: Connection) -> None:
    """その日の事実を集め advisor_journal を 1 件生成し proposal を起票する（spec §5・ADR-018）。

    1. policy 読み・briefing 収集・直近 journal 要約。
    2. build_messages（夜の定型指示・screen_context=None＝ADR-025）。
    3. run_tool_loop（source="nightly"）で事実を Tool で取り直し submit_journal を呼ばせる。
    4. tool_runs から submit_journal の args を取り出す（無ければ reply を observations に）。
    5. insert_journal（date=今日・source='nightly'・situation_briefing/policy_snapshot=JSON）。
    6. proposed_policy_change があれば insert_proposal（kind=policy_change・pending）。
    失敗時はその日の journal をスキップし notify.error を呼ぶ（例外は握る・ADR-018）。
    """
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    policy = repo.get_policy(conn)
    # 非同期コンテキスト内なので handler を直接 await する（同期 collect は asyncio.run で衝突）。
    briefing = await _gather_briefing()
    recent = repo.get_recent_journal_summary(conn)

    messages = build_messages(
        core_prompt=_CORE,
        policy=policy,
        conversation=[Message(role="user", content=_NIGHTLY_INSTRUCTION)],
        screen_context=None,  # 軸1 は画面が無い（ADR-025）
        recent_journal=recent,
    )

    try:
        reply, tool_runs = await run_tool_loop(messages, phase=CURRENT_PHASE, source="nightly")
    except Exception as exc:  # noqa: BLE001 — LLM 失敗は journal をスキップして通知（ADR-018）
        logger.error("夜の分析AI が失敗（journal スキップ）: %s", exc, exc_info=True)
        notify.error("夜の分析AI 失敗", f"LLM 呼び出しに失敗し当日の投資日記をスキップ: {exc}")
        return

    # tool_runs から submit_journal の最終 args を拾う（複数回呼ばれたら最後を採用）。
    submitted = _extract_submit_journal(tool_runs)
    if submitted is not None:
        observations = str(submitted.get("observations") or reply or "")
        proposal = submitted.get("proposal")
        raw_change = submitted.get("proposed_policy_change")
    else:
        # submit_journal 不呼び出し時は最終テキストを所見として残す（journal は欠かさない）。
        observations = reply or ""
        proposal = None
        raw_change = None

    # 変更案を単一 {field,to} に正規化（多列 patch 等は None＝適用不能な提案を起票しない）。
    # 正規化済み dict は apply_policy_change がそのまま食える形（ADR-013/018・U-10 裁定①）。
    proposed_change = coerce_policy_change(raw_change)
    if raw_change is not None and proposed_change is None:
        logger.warning(
            "夜の分析AI: proposed_policy_change が単一 {field,to} 形でない。"
            "提案は起票せず journal のみ記録する（ADR-013/018）。"
        )

    proposed_change_json = (
        json.dumps(proposed_change, ensure_ascii=False) if proposed_change else None
    )

    journal_id = repo.insert_journal(
        conn,
        date=today,
        source="nightly",
        situation_briefing=json.dumps(briefing, ensure_ascii=False),
        observations=observations,
        proposal=proposal if isinstance(proposal, str) else None,
        proposed_policy_change=proposed_change_json,
        policy_snapshot=json.dumps(policy, ensure_ascii=False) if policy is not None else None,
        llm_model=settings.llm_model,
    )

    # 方針変更案があれば承認制の提案として起票する（kind=policy_change・pending・§6.5）。
    # proposed_change は正規化済み（None なら起票せず＝適用不能な提案を回避）。
    if proposed_change:
        reason = proposed_change.get("reason")
        repo.insert_proposal(
            conn,
            created_date=today,
            kind="policy_change",
            body=proposed_change_json,
            rationale=str(reason) if reason else None,
            status="pending",
            journal_id=journal_id,
        )


def _extract_submit_journal(
    tool_runs: list[dict[str, object]],
) -> dict[str, object] | None:
    """tool_runs から submit_journal の args を取り出す（最後の呼び出しを採用・spec §5）。"""
    submitted: dict[str, object] | None = None
    for run in tool_runs:
        if run.get("name") == "submit_journal":
            args = run.get("args")
            if isinstance(args, dict):
                submitted = args
    return submitted
