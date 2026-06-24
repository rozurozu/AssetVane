"""LLM provider ディスパッチャ engine の振り分けを検証する（ADR-012/058）。

担保すること:
- run_turn: face.provider に応じて run_tool_loop（openai・face を渡す）か
  codex_engine.run_turn（codex・model を渡す）を呼ぶ。
- generate_once: openai は complete().content（face を渡す）、codex は codex_engine.generate_once。

resolve_face（DB 解決）はモックして、振り分けロジックだけを単体で見る（ネットに出ない・DB に触れ
ない＝testing-strategy）。resolve_face 自体の DB 解決は test_llm_config.py で検証する。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.advisor import codex_engine, engine
from app.advisor.llm import LLMResponse
from app.services.llm_config import ResolvedFace


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _openai_face(model: str = "m") -> ResolvedFace:
    return ResolvedFace(
        face="chat", provider="openai", base_url="https://x/v1", api_key="k", model=model
    )


def _codex_face(model: str = "gpt-5.5") -> ResolvedFace:
    return ResolvedFace(face="chat", provider="codex", base_url=None, api_key=None, model=model)


def test_run_turn_routes_to_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    """provider=openai のとき run_tool_loop（従来経路）に face を渡して振る。"""
    monkeypatch.setattr(engine, "resolve_face", lambda source: _openai_face())
    captured: dict[str, Any] = {}

    async def _fake_loop(messages: Any, **kw: Any) -> tuple[str, list[dict[str, object]]]:
        captured.update(kw)
        return "openai応答", [{"name": "get_signals", "args": {}}]

    monkeypatch.setattr(engine, "run_tool_loop", _fake_loop)
    text, runs = _run(engine.run_turn([{"role": "user", "content": "x"}], source="chat"))
    assert text == "openai応答"
    assert runs == [{"name": "get_signals", "args": {}}]
    # face が run_tool_loop に渡る（provider/model の伝播）。
    assert isinstance(captured.get("face"), ResolvedFace)
    assert captured["face"].provider == "openai"


def test_run_turn_routes_to_codex(monkeypatch: pytest.MonkeyPatch) -> None:
    """provider=codex のとき codex_engine.run_turn に model を渡して振る。"""
    monkeypatch.setattr(engine, "resolve_face", lambda source: _codex_face("gpt-5.5"))
    captured: dict[str, Any] = {}

    async def _fake_codex(messages: Any, **kw: Any) -> tuple[str, list[dict[str, object]]]:
        captured.update(kw)
        return "codex応答", []

    monkeypatch.setattr(codex_engine, "run_turn", _fake_codex)
    text, runs = _run(engine.run_turn([{"role": "user", "content": "x"}], source="chat"))
    assert text == "codex応答"
    assert runs == []
    assert captured.get("model") == "gpt-5.5"


def test_generate_once_openai_returns_content(monkeypatch: pytest.MonkeyPatch) -> None:
    """provider=openai の generate_once は complete().content を返す（face を渡す）。"""
    monkeypatch.setattr(engine, "resolve_face", lambda source: _openai_face())
    captured: dict[str, Any] = {}

    async def _fake_complete(messages: Any, **kw: Any) -> LLMResponse:
        captured.update(kw)
        return LLMResponse(content="本文", tool_calls=[])

    monkeypatch.setattr(engine, "complete", _fake_complete)
    text = _run(engine.generate_once([{"role": "user", "content": "x"}], source="dossier"))
    assert text == "本文"
    assert isinstance(captured.get("face"), ResolvedFace)


def test_generate_once_codex(monkeypatch: pytest.MonkeyPatch) -> None:
    """provider=codex の generate_once は codex_engine.generate_once を返す（model を渡す）。"""
    monkeypatch.setattr(engine, "resolve_face", lambda source: _codex_face("gpt-5.5"))
    captured: dict[str, Any] = {}

    async def _fake_codex(messages: Any, **kw: Any) -> str:
        captured.update(kw)
        return "codex本文"

    monkeypatch.setattr(codex_engine, "generate_once", _fake_codex)
    text = _run(engine.generate_once([{"role": "user", "content": "x"}], source="dossier"))
    assert text == "codex本文"
    assert captured.get("model") == "gpt-5.5"
