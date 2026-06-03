"""軸2 相談チャット API のテスト（POST /chat・phase3-spec.md §6.3・§10）。

`client` フィクスチャで叩き、LLM（complete）は必ずモック（ネットを叩かない）。検証対象:
- {reply} 契約維持・tool_runs が返る・context 付きで通る。
- CostGuardError → 429。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.advisor import service
from app.advisor.llm import CostGuardError, LLMResponse, ToolCall


def test_chat_returns_reply(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """complete をモックして {reply} 契約が維持されること。"""

    async def _fake_complete(messages: Any, **_: Any) -> LLMResponse:
        # CORE/POLICY が system に積まれていることを軽く確認。
        assert messages[0]["role"] == "system"
        return LLMResponse(content="こんにちは", tool_calls=[])

    monkeypatch.setattr(service, "complete", _fake_complete)

    res = client.post("/chat", json={"messages": [{"role": "user", "content": "やあ"}]})
    assert res.status_code == 200
    body = res.json()
    assert body["reply"] == "こんにちは"
    assert body["tool_runs"] == []


def test_chat_with_context_and_tool_runs(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """context 付き・Tool ループを 1 往復してから tool_runs を返す（結果値は載らない）。"""
    responses = [
        LLMResponse(
            content=None,
            tool_calls=[ToolCall(id="c1", name="get_signals", arguments={"type": "momentum"})],
        ),
        LLMResponse(content="兆候を確認しました", tool_calls=[]),
    ]

    async def _fake_complete(messages: Any, **_: Any) -> LLMResponse:
        return responses.pop(0)

    async def _fake_handler(_args: dict[str, object]) -> dict[str, object]:
        return {"signals": [{"code": "7203", "score": 0.95}]}

    monkeypatch.setattr(service, "complete", _fake_complete)
    base = service.REGISTRY["get_signals"]
    from app.advisor.tools.registry import ToolDef

    monkeypatch.setitem(
        service.REGISTRY,
        "get_signals",
        ToolDef(
            name=base.name,
            description=base.description,
            parameters=base.parameters,
            handler=_fake_handler,
            min_phase=base.min_phase,
        ),
    )

    res = client.post(
        "/chat",
        json={
            "messages": [{"role": "user", "content": "兆候は?"}],
            "context": {"page": "signals", "focus": {"type": "stock", "code": "7203"}},
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["reply"] == "兆候を確認しました"
    assert body["tool_runs"] == [{"name": "get_signals", "args": {"type": "momentum"}}]
    # 結果の数値（score 等）は tool_runs に載らない（ADR-025）。
    import json

    assert "0.95" not in json.dumps(body["tool_runs"])


def test_chat_cost_guard_returns_429(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """CostGuardError は 429 に翻訳される（spec §7.1）。"""

    async def _raise_guard(messages: Any, **_: Any) -> LLMResponse:
        raise CostGuardError("LLM 月額コスト上限超過")

    monkeypatch.setattr(service, "complete", _raise_guard)

    res = client.post("/chat", json={"messages": [{"role": "user", "content": "x"}]})
    assert res.status_code == 429
    assert "上限" in res.json()["detail"]
