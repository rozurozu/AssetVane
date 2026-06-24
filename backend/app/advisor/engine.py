"""LLM provider ディスパッチャ（面別に provider/model を解決して openai/codex へ振り分け）。

設計の出所: docs/decisions.md ADR-012 の延長（codex 接続）＋ ADR-058（provider/model を
env→DB+WebUI に移管・面別に複数 provider を割当）。

LLM 呼び出しには 2 形態がある:
- run_turn: エージェント形（Tool ループ）。chat / nightly が使う。
- generate_once: 単発テキスト形（Tool 無し）。dossier 要約・tagger が使う。

各形態を source（chat/nightly/dossier/tagger）で resolve_face し、解決した面の provider 経路へ
振り分ける。provider="openai"（OpenAI 互換・鍵あり）は従来経路（service.run_tool_loop /
llm.complete）へ face を渡して呼ぶ。provider="codex"（鍵なし組み込み）は codex_engine へ model を
渡して回す。未設定/宙づりは resolve_face が FaceNotConfiguredError を投げ、呼び出し元が
ADR-018 準拠で処理する（chat=明示エラー / nightly・dossier=通知付き skip / tagger=沈黙 skip）。
"""

from __future__ import annotations

from app.advisor import codex_engine
from app.advisor.llm import complete
from app.advisor.service import run_tool_loop
from app.advisor.tools.registry import CURRENT_PHASE
from app.db.engine import get_engine
from app.services import llm_config
from app.services.llm_config import ResolvedFace


def resolve_face(source: str) -> ResolvedFace:
    """source（面）から ResolvedFace を解決する（毎回 DB を引く＝UI 変更を即時反映・ADR-058）。

    face 行は 4 行しかなく connect は軽い（_check_cost_guard と同じ前例）。未設定/宙づりは
    FaceNotConfiguredError を投げる（呼び出し元が ADR-018 で処理）。
    """
    with get_engine().connect() as conn:
        return llm_config.resolve_face(conn, source)


async def run_turn(
    messages: list[dict[str, object]],
    *,
    phase: int = CURRENT_PHASE,
    source: str = "chat",
) -> tuple[str, list[dict[str, object]]]:
    """エージェント形（Tool ループ）を面の provider に振り分ける（ADR-058）。

    戻り値は (最終テキスト, tool_runs)。tool_runs は [{name, args}]（結果値なし・ADR-025）で、
    provider 差なく同じ形（openai は run_tool_loop が、codex は MCP の run 記録が供給する）。
    """
    face = resolve_face(source)
    if face.provider == "codex":
        return await codex_engine.run_turn(
            messages,
            phase=phase,
            source=source,
            model=face.model,
            reasoning_effort=face.reasoning_effort,
        )
    return await run_tool_loop(messages, face=face, phase=phase, source=source)


async def generate_once(messages: list[dict[str, object]], *, source: str) -> str:
    """単発テキスト形（Tool 無し）を面の provider に振り分け、最終テキストを返す（ADR-058）。

    openai は llm.complete の content をそのまま返す（呼び出し側が JSON パース等を行う）。
    """
    face = resolve_face(source)
    if face.provider == "codex":
        return await codex_engine.generate_once(
            messages, source=source, model=face.model, reasoning_effort=face.reasoning_effort
        )
    resp = await complete(messages, face=face, source=source)
    return resp.content or ""
