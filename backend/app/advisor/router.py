"""AI Advisor の REST ルータ（軸2・相談チャット）。

設計の真実: docs/phase-specs/phase3-spec.md §6.3・ADR-014/015/024/025。

`POST /chat`（api.md §4）。CORE（不変・リポジトリ）＋ POLICY（DB）＋ 文脈（直近 journal）＋
画面コンテキスト（軽量ヒント）を build_messages で組み、Tool ループ（service.run_tool_loop）で
事実を Tool 経由で引きながら最終応答を返す（ADR-014）。サーバはステートレスで、会話履歴は
frontend が保持し毎ターン messages 配列で送る（ADR-024・§6.4）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from fastapi import APIRouter, HTTPException
from openai import OpenAIError
from pydantic import BaseModel

from app.advisor.codex_engine import CodexEngineError
from app.advisor.core_prompt import CORE
from app.advisor.engine import resolve_face, run_turn
from app.advisor.journaling import (
    persist_journal_from_tool_runs,
    persist_trade_proposals_from_tool_runs,
)
from app.advisor.llm import CostGuardError
from app.advisor.prompt_builder import Message, ScreenContext, build_messages
from app.advisor.tools.registry import CURRENT_PHASE
from app.db import repo
from app.db.engine import get_engine
from app.services import policy as policy_service
from app.services.knowledge_cards import load_active_card_texts
from app.services.llm_config import FaceNotConfiguredError

router = APIRouter(tags=["advisor"])


class ChatRequest(BaseModel):
    """`POST /chat` のリクエスト（spec §6.3）。messages は user/assistant のみ（system 不可）。"""

    messages: list[Message]
    context: ScreenContext | None = None  # 画面コンテキスト（軽量ヒント・ADR-025）


class ToolRun(BaseModel):
    """チャットが呼んだ Tool の記録（UI 可視化用・spec §4.2）。結果の数値は載せない（ADR-025）。"""

    name: str
    args: dict[str, object] | None = None


class ChatResponse(BaseModel):
    """`POST /chat` のレスポンス（spec §6.3）。{reply} 契約は維持し tool_runs を足すだけ。

    journal_id: チャットで submit_journal を呼んで投資日記に記録できたときの id（ADR-029）。
    呼ばれなかった・observations 空でスキップしたときは None。frontend の「日記に残した」表示用。
    """

    reply: str
    tool_runs: list[ToolRun] = []
    journal_id: int | None = None


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    """相談チャット。POLICY/文脈/画面コンテキストを組み、Tool ループで応答を返す（spec §6.3）。"""
    # POLICY（DEFAULT マージ済み）と直近 journal は読み取り接続で引く（ADR-005）。
    with get_engine().connect() as conn:
        policy = policy_service.get_policy(conn)
        recent = repo.get_recent_journal_summary(conn)

    messages = build_messages(
        core_prompt=CORE,
        policy=policy,
        conversation=req.messages,
        screen_context=req.context,
        knowledge_cards=load_active_card_texts(),
        recent_journal=recent,
    )

    # 面（provider/model）は engine が source="chat" から解決する（ADR-058）。未設定なら明示エラー
    # （対話チャットなので Discord 通知はしない＝ADR-018・確定8）。journal の監査 model にも使う。
    try:
        face = resolve_face("chat")
    except FaceNotConfiguredError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"chat 面の LLM が未設定です（/settings で provider/model を割り当て）: {exc}",
        ) from exc

    try:
        reply, tool_runs = await run_turn(messages, phase=CURRENT_PHASE, source="chat")
    except CostGuardError as exc:
        # 月額コスト上限超過（block）。frontend が detail を吹き出しに出す（spec §7.1・ADR-028）。
        raise HTTPException(
            status_code=429,
            detail=f"LLM 月額上限超過のため応答できません: {exc}",
        ) from exc
    except (OpenAIError, CodexEngineError) as exc:
        # 対話的なチャットなので Discord 通知はしない（あれは無人バッチ＝ADR-018）。
        # OpenAIError=API 経路の接続失敗、CodexEngineError=codex 経路の失敗（自動フォールバック
        # しない＝plans）。どちらも 502 で返し frontend が再試行を促す。
        raise HTTPException(
            status_code=502,
            detail=f"LLM への接続に失敗しました（provider / base_url / codex login を確認）: {exc}",
        ) from exc

    response_tool_runs: list[ToolRun] = []
    for run in tool_runs:
        name = run.get("name")
        if not isinstance(name, str):
            continue
        args_raw = run.get("args")
        args = cast(dict[str, object], args_raw) if isinstance(args_raw, dict) else None
        response_tool_runs.append(ToolRun(name=name, args=args))

    # チャットが submit_journal を呼んだときだけ投資日記に記録する（明示要求時のみ＝ADR-029）。
    # propose_trade（ADR-052）は journal とは独立に buy/sell 提案を起票する＝submit が無くても
    # 起票するが、journal は has_submit のときだけ書く（「明示 submit がなければ日記は残さない」
    # 不変条件を保つ）。通常ターン（どちらも無し）では書き込み接続を開かない。橋渡しは nightly と
    # 共通の journaling サービスに一本化（W2＝begin() で journal＋proposal を atomic に束ねる）。
    journal_id: int | None = None
    has_submit = any(r.get("name") == "submit_journal" for r in tool_runs)
    has_trade = any(r.get("name") == "propose_trade" for r in tool_runs)
    if has_submit or has_trade:
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        with get_engine().begin() as conn:
            if has_submit:
                journal_id = persist_journal_from_tool_runs(
                    conn,
                    tool_runs=tool_runs,
                    reply=reply,
                    source="chat",
                    date=today,
                    situation_briefing=None,  # 軸2 は画面コンテキストのみで監査 briefing は持たない
                    policy=policy,
                    llm_model=face.model,  # 面別に解決された実 model を監査に残す（ADR-058）
                )
            # buy/sell 提案を起票（journal_id があれば紐付け・無ければ独立＝journal_id=None）。
            persist_trade_proposals_from_tool_runs(
                conn, tool_runs=tool_runs, date=today, journal_id=journal_id
            )

    return ChatResponse(reply=reply, tool_runs=response_tool_runs, journal_id=journal_id)
