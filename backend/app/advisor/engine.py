"""LLM provider ディスパッチャ（openai / codex を source で面別に切替・plans / ADR-012）。

設計の出所: docs/decisions.md ADR-012 の延長（codex 接続）/ plans。

LLM 呼び出しには 2 形態がある:
- run_turn: エージェント形（Tool ループ）。chat / nightly が使う。
- generate_once: 単発テキスト形（Tool 無し）。dossier 要約が使う。

各形態を source（chat/nightly/dossier）で解決した provider に振り分ける。既定はすべて openai＝
従来経路（service.run_tool_loop / llm.complete）を**無改修のまま**呼ぶ。codex を選んだ source は
codex_engine（codex exec＋FastAPI 内 MCP）に回す。「今まで通り API でも動く」を常に残す（plans）。
"""

from __future__ import annotations

from app.advisor import codex_engine
from app.advisor.llm import complete
from app.advisor.service import run_tool_loop
from app.advisor.tools.registry import CURRENT_PHASE
from app.config import settings


def resolve_provider(source: str) -> str:
    """source から provider（"openai"/"codex"）を解決する（既定 openai・plans）。"""
    return settings.provider_for(source)


async def run_turn(
    messages: list[dict[str, object]],
    *,
    phase: int = CURRENT_PHASE,
    source: str = "chat",
) -> tuple[str, list[dict[str, object]]]:
    """エージェント形（Tool ループ）を provider に振り分ける。

    戻り値は (最終テキスト, tool_runs)。tool_runs は [{name, args}]（結果値なし・ADR-025）で、
    provider 差なく同じ形（openai は run_tool_loop が、codex は MCP の run 記録が供給する）。
    """
    if resolve_provider(source) == "codex":
        return await codex_engine.run_turn(messages, phase=phase, source=source)
    return await run_tool_loop(messages, phase=phase, source=source)


async def generate_once(messages: list[dict[str, object]], *, source: str) -> str:
    """単発テキスト形（Tool 無し）を provider に振り分け、最終テキストを返す。

    openai は llm.complete の content をそのまま返す（呼び出し側が JSON パース等を行う）。
    """
    if resolve_provider(source) == "codex":
        return await codex_engine.generate_once(messages, source=source)
    resp = await complete(messages, source=source)
    return resp.content or ""
