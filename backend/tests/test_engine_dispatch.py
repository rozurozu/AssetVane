"""LLM provider ディスパッチャ engine の振り分けを検証する（plans・ADR-012）。

担保すること:
- resolve_provider: source × settings で openai/codex を解決（既定 openai）。
- run_turn: provider に応じて run_tool_loop（openai）か codex_engine.run_turn（codex）を呼ぶ。
- generate_once: openai は complete().content、codex は codex_engine.generate_once。

ネットに出ない（codex_engine / service / llm をモック）＝testing-strategy。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.advisor import codex_engine, engine
from app.advisor.llm import LLMResponse
from app.config import settings


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_resolve_provider_defaults_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    """未設定/未知 source は openai。設定すれば codex を返す。"""
    monkeypatch.setattr(settings, "llm_provider_chat", "openai")
    monkeypatch.setattr(settings, "llm_provider_nightly", "codex")
    assert engine.resolve_provider("chat") == "openai"
    assert engine.resolve_provider("nightly") == "codex"
    assert engine.resolve_provider("unknown") == "openai"


def test_run_turn_routes_to_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    """provider=openai のとき run_tool_loop（従来経路）に振る。"""
    monkeypatch.setattr(settings, "llm_provider_chat", "openai")

    async def _fake_loop(messages: Any, **_: Any) -> tuple[str, list[dict[str, object]]]:
        return "openai応答", [{"name": "get_signals", "args": {}}]

    monkeypatch.setattr(engine, "run_tool_loop", _fake_loop)
    text, runs = _run(engine.run_turn([{"role": "user", "content": "x"}], source="chat"))
    assert text == "openai応答"
    assert runs == [{"name": "get_signals", "args": {}}]


def test_run_turn_routes_to_codex(monkeypatch: pytest.MonkeyPatch) -> None:
    """provider=codex のとき codex_engine.run_turn に振る。"""
    monkeypatch.setattr(settings, "llm_provider_chat", "codex")

    async def _fake_codex(messages: Any, **_: Any) -> tuple[str, list[dict[str, object]]]:
        return "codex応答", []

    monkeypatch.setattr(codex_engine, "run_turn", _fake_codex)
    text, runs = _run(engine.run_turn([{"role": "user", "content": "x"}], source="chat"))
    assert text == "codex応答"
    assert runs == []


def test_generate_once_openai_returns_content(monkeypatch: pytest.MonkeyPatch) -> None:
    """provider=openai の generate_once は complete().content を返す。"""
    monkeypatch.setattr(settings, "llm_provider_dossier", "openai")

    async def _fake_complete(messages: Any, **_: Any) -> LLMResponse:
        return LLMResponse(content="本文", tool_calls=[])

    monkeypatch.setattr(engine, "complete", _fake_complete)
    text = _run(engine.generate_once([{"role": "user", "content": "x"}], source="dossier"))
    assert text == "本文"


def test_generate_once_codex(monkeypatch: pytest.MonkeyPatch) -> None:
    """provider=codex の generate_once は codex_engine.generate_once を返す。"""
    monkeypatch.setattr(settings, "llm_provider_dossier", "codex")

    async def _fake_codex(messages: Any, **_: Any) -> str:
        return "codex本文"

    monkeypatch.setattr(codex_engine, "generate_once", _fake_codex)
    text = _run(engine.generate_once([{"role": "user", "content": "x"}], source="dossier"))
    assert text == "codex本文"
