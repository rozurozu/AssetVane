"""LLM 面別ディスパッチャ（面別に provider/model を解決して呼び出し形態へ振り分け）。

設計の出所: docs/decisions.md ADR-058（provider/model を env→DB+WebUI に移管・面別に複数
provider を割当）。codex 経路は ADR-073 で撤去し、provider は OpenAI 互換のみ。

LLM 呼び出しには 2 形態がある:
- run_turn: エージェント形（Tool ループ）。chat / nightly が使う。
- generate_once: 単発テキスト形（Tool 無し）。dossier 要約・tagger が使う。

各形態を source（chat/nightly/dossier/tagger）で resolve_face し、解決した面の provider
（OpenAI 互換・鍵あり）へ face を渡して呼ぶ（service.run_tool_loop / llm.complete）。未設定/宙づり
は resolve_face が FaceNotConfiguredError を投げ、呼び出し元が ADR-018 準拠で処理する
（chat=明示エラー / nightly・dossier=通知付き skip / tagger=沈黙 skip）。
"""

from __future__ import annotations

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
    run_tool_loop が供給する。
    """
    face = resolve_face(source)
    return await run_tool_loop(messages, face=face, phase=phase, source=source)


async def generate_once(messages: list[dict[str, object]], *, source: str) -> str:
    """単発テキスト形（Tool 無し）を面の provider に振り分け、最終テキストを返す（ADR-058）。

    llm.complete の content をそのまま返す（呼び出し側が JSON パース等を行う）。
    """
    face = resolve_face(source)
    resp = await complete(messages, face=face, source=source)
    return resp.content or ""
