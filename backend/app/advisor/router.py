"""AI Advisor の REST ルータ（軸2・相談チャット）。

設計の真実: docs/phase-specs/phase3-spec.md §6.3・ADR-014/015/024/025。

`POST /chat`（api.md §4）。CORE（不変・リポジトリ）＋ POLICY（DB）＋ 文脈（直近 journal）＋
画面コンテキスト（軽量ヒント）を build_messages で組み、Tool ループ（service.run_tool_loop）で
事実を Tool 経由で引きながら最終応答を返す（ADR-014）。サーバはステートレスで、会話履歴は
frontend が保持し毎ターン messages 配列で送る（ADR-024・§6.4）。
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from fastapi import APIRouter, HTTPException
from openai import OpenAIError
from pydantic import BaseModel

from app.advisor.codex_engine import CodexEngineError
from app.advisor.engine import run_turn
from app.advisor.llm import CostGuardError
from app.advisor.method_cards import METHOD_CARDS
from app.advisor.prompt_builder import Message, ScreenContext, build_messages
from app.advisor.tools.registry import CURRENT_PHASE
from app.db import repo
from app.db.engine import get_engine
from app.services import policy as policy_service

router = APIRouter(tags=["advisor"])

# CORE プロンプトはリポジトリ内のファイル（ADR-015）。意図的なコミットでしか変わらない。
# 起動時に1度だけ読む（チャットでは書き換えない）。
_CORE = (Path(__file__).parent / "core_prompt.md").read_text(encoding="utf-8")


class ChatRequest(BaseModel):
    """`POST /chat` のリクエスト（spec §6.3）。messages は user/assistant のみ（system 不可）。"""

    messages: list[Message]
    context: ScreenContext | None = None  # 画面コンテキスト（軽量ヒント・ADR-025）


class ToolRun(BaseModel):
    """チャットが呼んだ Tool の記録（UI 可視化用・spec §4.2）。結果の数値は載せない（ADR-025）。"""

    name: str
    args: dict[str, object] | None = None


class ChatResponse(BaseModel):
    """`POST /chat` のレスポンス（spec §6.3）。{reply} 契約は維持し tool_runs を足すだけ。"""

    reply: str
    tool_runs: list[ToolRun] = []


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    """相談チャット。POLICY/文脈/画面コンテキストを組み、Tool ループで応答を返す（spec §6.3）。"""
    # POLICY（DEFAULT マージ済み）と直近 journal は読み取り接続で引く（ADR-005）。
    with get_engine().connect() as conn:
        policy = policy_service.get_policy(conn)
        recent = repo.get_recent_journal_summary(conn)

    messages = build_messages(
        core_prompt=_CORE,
        policy=policy,
        conversation=req.messages,
        screen_context=req.context,
        method_cards=METHOD_CARDS,
        recent_journal=recent,
    )

    try:
        # provider（openai/codex）は engine が source="chat" から解決する（plans・ADR-012）。
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

    return ChatResponse(reply=reply, tool_runs=response_tool_runs)
