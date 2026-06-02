"""AI Advisor の REST ルータ（軸2・相談チャット）。

`POST /chat`（api.md §4）。いまは「ただ LLM と話すだけ」の最小実装。
サーバはステートレスで、会話履歴は frontend が保持して毎ターン messages 配列で送る。

プロンプトの組み立て（CORE を先頭に差し込む）はここで行い、LLM アダプタ（llm.py）は
運搬役に徹する（ADR-015）。POLICY・Tool・手法カード・文脈・画面コンテキストは後続フェーズで
この組み立てに「層」として足していく（docs/advisor.md §6）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from openai import OpenAIError
from pydantic import BaseModel

from app.advisor.llm import complete

router = APIRouter(tags=["advisor"])

# CORE プロンプトはリポジトリ内のファイル（ADR-015）。意図的なコミットでしか変わらない。
# 起動時に1度だけ読む（チャットでは書き換えない）。
_CORE_PROMPT = (Path(__file__).parent / "core_prompt.md").read_text(encoding="utf-8")


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    messages: list[Message]
    # TODO(adr-025): 画面コンテキスト（page / focus）を context として受け取り、1 行の自然文に
    #   compile して system の後ろに差す。数値は載せない（必要なら AI が Tool で取り直す）。


class ChatResponse(BaseModel):
    reply: str


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    """相談チャット。CORE を先頭に差し、会話を LLM に渡して応答を返すだけ。"""
    # 組み立て: [CORE(system)] + 会話履歴。
    # TODO(advisor): ここに POLICY（DB の policy をコンパイル）・手法カード・Tool Calling の
    #   事実・直近の投資日記（文脈）を層として足す（docs/advisor.md §6）。
    messages: list[dict[str, str]] = [{"role": "system", "content": _CORE_PROMPT}]
    messages += [{"role": m.role, "content": m.content} for m in req.messages]

    try:
        reply = await complete(messages)
    except OpenAIError as exc:
        # 対話的なチャットなので、ここでは Discord 通知はしない（あれは無人バッチ＝ADR-018）。
        # frontend がこの detail をエラー吹き出しとして表示する。
        raise HTTPException(
            status_code=502,
            detail=f"LLM への接続に失敗しました（base_url / モデル / Ollama 稼働を確認）: {exc}",
        ) from exc

    return ChatResponse(reply=reply)
