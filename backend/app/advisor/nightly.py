"""軸1 夜の分析AI（spec §5・ADR-011/014/018/025）。

設計の真実: docs/phase-specs/phase3-spec.md §5。

cron 夜間バッチ（Phase 1 導入）に相乗りし、「昨日までの方針（policy）」と「今日の事実」を
突き合わせて方針見直しを提案し、advisor_journal を 1 件生成して proposal を起票する。
画面コンテキストは無い（ADR-025）。出力は専用 Tool `submit_journal` で受ける（spec §5・決定7）。

障害時（ADR-018）: LLM 失敗（OpenAIError/CostGuardError 等）は complete 側のリトライで吸収し、
最終的に失敗したら例外を run_turn からそのまま上位（run_advisor ジョブ）へ伝播させる。
無応答（observations 空＝実質何もしなかった晩）は理由文字列を return する。いずれの場合も
当日 journal をスキップし、通知は呼び出し側（run_advisor ジョブ）経由で runner 集約が担う
（nightly 自身は notify しない）。conn は呼び出し側（run_advisor ジョブ）が begin() で渡す。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime

from sqlalchemy import Connection

from app.advisor.core_prompt import CORE
from app.advisor.engine import resolve_face, run_turn
from app.advisor.journaling import (
    persist_card_ops_from_tool_runs,
    persist_journal_from_tool_runs,
    persist_trade_proposals_from_tool_runs,
)
from app.advisor.prompt_builder import Message, build_messages
from app.advisor.tools import handlers
from app.advisor.tools.registry import CURRENT_PHASE
from app.db import repo
from app.services.knowledge_cards import load_card_texts_for_injection

logger = logging.getLogger(__name__)

# 夜の定型指示文（spec §5）。画面は無いので「今日の事実を Tool で取り直して突き合わせよ」。
_NIGHTLY_INSTRUCTION = (
    "あなたは夜間の自動分析を担っている。利用可能な Tool（get_signals / get_portfolio_metrics / "
    "get_asset_overview 等）で今日の事実を取り直し、昨日までの方針と突き合わせて、見直しが必要なら"
    "方針変更を提案せよ。get_general_news で当日の一般ニュース（市況・マクロ・世界情勢）も取得し、"
    "市況・マクロ文脈を踏まえて分析せよ（ADR-034）。最後に必ず submit_journal で所見"
    "（observations）・提案（proposal）・方針変更案（proposed_policy_change）を提出すること。"
    "強い買い/売り材料がある銘柄があれば propose_trade で方向と根拠を起票せよ（無ければ"
    "出さなくてよい・数値は出さない＝ADR-052）。数値は必ず Tool の戻り値のみを使う。"
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
    return asyncio.run(_gather_briefing())


async def run_nightly_advisor(conn: Connection) -> str | None:
    """その日の事実を集め advisor_journal を 1 件生成し proposal を起票する（spec §5・ADR-018）。

    1. policy 読み・briefing 収集・直近 journal 要約。
    2. build_messages（夜の定型指示・screen_context=None＝ADR-025）。
    3. run_tool_loop（source="nightly"）で事実を Tool で取り直し submit_journal を呼ばせる。
    4. tool_runs から submit_journal の args を取り出す（無ければ reply を observations に）。
    5. observations が空（縮退した晩）なら journal を書かず理由文字列を return する。
    6. insert_journal（date=今日・source='nightly'・situation_briefing/policy_snapshot=JSON）。
    7. proposed_policy_change があれば insert_proposal（kind=policy_change・pending）。

    戻り値: None=成功（journal 1 件記録）。str=縮退スキップ理由（observations 空で journal なし）。
    LLM 失敗（例外）は握らず run_turn からそのまま上位（run_advisor ジョブ）へ伝播させる。
    いずれも当日 journal をスキップし、通知は呼び出し側経由で runner 集約が担う（ADR-018）。
    """
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    # nightly 面を先に解決（未設定/宙づりは FaceNotConfiguredError → 上位 run_advisor ジョブが
    # ok=False に集約し runner が通知＝ADR-018/058）。journal の監査 model にも使う。
    face = resolve_face("nightly")
    policy = repo.get_policy(conn)
    # 非同期コンテキスト内なので handler を直接 await する（同期 collect は asyncio.run で衝突）。
    briefing = await _gather_briefing()
    recent = repo.get_recent_journal_summary(conn)

    messages = build_messages(
        core_prompt=CORE,
        policy=policy,
        conversation=[Message(role="user", content=_NIGHTLY_INSTRUCTION)],
        screen_context=None,  # 軸1 は画面が無い（ADR-025）
        # 夜AIは ambient（市況/一般）のみ＋具体は search_cards Tool で深掘り（ADR-062）。
        knowledge_cards=await load_card_texts_for_injection(None),
        recent_journal=recent,
    )

    # provider（openai/codex）は engine が source="nightly" から解決する（plans・ADR-012）。
    # LLM 失敗（②ハード失敗）は握らず上位（run_advisor ジョブ）へ伝播させる（ADR-018）。
    reply, tool_runs = await run_turn(messages, phase=CURRENT_PHASE, source="nightly")

    # tool_runs → journal/proposal の橋渡しは共通サービスへ一本化（軸2 /chat と同じ真実）。
    # 戻り値 None＝observations 空（縮退）。journal_id（int）＝記録成功（ADR-018/029）。
    journal_id = persist_journal_from_tool_runs(
        conn,
        tool_runs=tool_runs,
        reply=reply,
        source="nightly",
        date=today,
        situation_briefing=json.dumps(briefing, ensure_ascii=False),
        policy=policy,
        llm_model=face.model,  # 面別に解決された実 model を監査に残す（ADR-058）
    )

    # ニュース起点の buy/sell 提案を起票（ADR-052・journal とは独立＝縮退で journal_id=None でも
    # trade は起票する）。同一トランザクション（呼び出し側の begin()）で束ねる（W2）。
    persist_trade_proposals_from_tool_runs(
        conn, tool_runs=tool_runs, date=today, journal_id=journal_id
    )
    # 知識カードの起票/weight 変更を起票（ADR-062 追補・同一トランザクション）。
    persist_card_ops_from_tool_runs(conn, tool_runs=tool_runs, date=today)

    # 縮退した晩（例外なし・observations 空＝実質何もしなかった）は journal を書かず理由を返す。
    # submit_journal 未呼び出しでも reply 非空なら到達しない（フォールバック健全＝ADR-018）。
    # 既存契約（test_nightly）の理由文面を保つため、切り分け材料を nightly 側で組む。
    if journal_id is None:
        submit_called = any(r.get("name") == "submit_journal" for r in tool_runs)
        reason = (
            f"夜AI が無応答（observations 空）: submit_journal="
            f"{'有' if submit_called else '無'}・reply長={len(reply or '')}"
            f"・tool_runs={len(tool_runs)} 件。当日 journal をスキップ（ADR-018）。"
        )
        logger.warning(reason)
        return reason

    return None  # observations 非空で journal を記録できた＝成功（ADR-018）
