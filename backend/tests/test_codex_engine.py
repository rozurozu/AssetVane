"""codex_engine（provider="codex"・app-server 駆動）の純ロジック検証（plans・ADR-012/018/025）。

担保すること:
- _split_messages: 先頭 system→baseInstructions、2 番目以降 system→developerInstructions、
  会話→prompt（単発はそのまま／複数はトランスクリプト）。
- _is_transient: 一過性失敗マーカー（codexErrorInfo 由来）の判定。
- _drain_turn: app-server の通知列（item/completed の agentMessage / mcpToolCall・turn/completed）
  から最終テキストと tool_runs を再構成（結果値なし＝ADR-025）。turn 失敗は CodexEngineError。
- run_turn / generate_once: _AppServer.run へ正しい引数（with_tools）で委譲する。

ネットにもサブプロセスにも出ない（_AppServer をモック・通知 Queue を直接食わす）＝testing-strategy。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.advisor import codex_engine
from app.advisor.codex_engine import CodexEngineError, _AppServer


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


# --- _split_messages -------------------------------------------------------


def test_split_messages_single_user() -> None:
    """先頭 system→base、2 番目→developer、user 1 通なら本文がそのまま prompt（夜/ドシエ）。"""
    messages = [
        {"role": "system", "content": "CORE"},
        {"role": "system", "content": "POLICY"},
        {"role": "user", "content": "今日の兆候は?"},
    ]
    base, developer, prompt = codex_engine._split_messages(messages)
    assert base == "CORE"
    assert developer == "POLICY"
    assert prompt == "今日の兆候は?"


def test_split_messages_multi_turn_transcript() -> None:
    """複数ターンはラベル付きトランスクリプトに整形する（最後の user が今回の依頼）。"""
    messages = [
        {"role": "system", "content": "CORE"},
        {"role": "system", "content": "POLICY"},
        {"role": "system", "content": "画面: dashboard"},
        {"role": "user", "content": "A"},
        {"role": "assistant", "content": "B"},
        {"role": "user", "content": "C"},
    ]
    base, developer, prompt = codex_engine._split_messages(messages)
    assert base == "CORE"
    # 2 番目以降の system は developer に連結。
    assert developer == "POLICY\n\n画面: dashboard"
    assert "ユーザー: A" in prompt
    assert "アシスタント: B" in prompt
    assert prompt.strip().endswith("ユーザー: C")


def test_split_messages_no_system() -> None:
    """system が無ければ base/developer は空（防御的）。"""
    base, developer, prompt = codex_engine._split_messages([{"role": "user", "content": "x"}])
    assert base == ""
    assert developer == ""
    assert prompt == "x"


# --- _is_transient ---------------------------------------------------------


def test_is_transient_markers() -> None:
    """codexErrorInfo の一過性（serverOverloaded 等）は再試行、恒久（badRequest 等）はしない。"""
    assert codex_engine._is_transient("codex turn 失敗（chat・serverOverloaded）: ...")
    assert codex_engine._is_transient("usageLimitExceeded")
    assert codex_engine._is_transient("httpConnectionFailed at upstream")
    assert not codex_engine._is_transient("codex turn 失敗（chat・badRequest）: model not found")
    assert not codex_engine._is_transient("contextWindowExceeded")


# --- _drain_turn（通知列から集約）-----------------------------------------


def _server_with_events(events: list[tuple[str, dict[str, Any]]]) -> _AppServer:
    """通知列をあらかじめ Queue に積んだ _AppServer を作る（reader をモックする）。"""
    server = _AppServer()
    q: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
    for method, params in events:
        q.put_nowait((method, {"params": params}))
    server._event_q = q
    return server


def test_drain_turn_collects_text_and_tool_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    """agentMessage→最終テキスト、mcpToolCall(server=assetvane)→tool_runs（結果値なし＝ADR-025）。"""
    monkeypatch.setattr(codex_engine, "_record_usage", lambda *a, **k: None)
    events = [
        ("item/completed", {"item": {"type": "reasoning", "content": []}}),
        (
            "item/completed",
            {
                "item": {
                    "type": "mcpToolCall",
                    "server": "assetvane",
                    "tool": "get_signals",
                    "arguments": {"code": "7203"},
                    "result": {"signals": ["...大量..."]},  # ← これは tool_runs に載らない
                }
            },
        ),
        # 別サーバの tool 呼び出しは無視する。
        (
            "item/completed",
            {"item": {"type": "mcpToolCall", "server": "other", "tool": "x", "arguments": {}}},
        ),
        ("item/completed", {"item": {"type": "agentMessage", "text": "途中", "phase": "draft"}}),
        (
            "item/completed",
            {"item": {"type": "agentMessage", "text": "最終回答", "phase": "final_answer"}},
        ),
        ("thread/tokenUsage/updated", {"tokenUsage": {"total": {"inputTokens": 10}}}),
        ("turn/completed", {"turn": {"status": "completed"}}),
    ]
    server = _server_with_events(events)
    text, tool_runs = _run(server._drain_turn("th-1", source="chat"))
    assert text == "最終回答"
    assert tool_runs == [{"name": "get_signals", "args": {"code": "7203"}}]


def test_drain_turn_failed_status_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """turn.status=="failed" は CodexEngineError（codexErrorInfo をメッセージに載せる）。"""
    monkeypatch.setattr(codex_engine, "_record_usage", lambda *a, **k: None)
    events = [
        (
            "turn/completed",
            {
                "turn": {
                    "status": "failed",
                    "error": {"codexErrorInfo": "serverOverloaded", "message": "overloaded"},
                }
            },
        ),
    ]
    server = _server_with_events(events)
    with pytest.raises(CodexEngineError) as exc:
        _run(server._drain_turn("th-1", source="chat"))
    # 一過性マーカーが乗っていること（リトライ判定が効く）。
    assert codex_engine._is_transient(str(exc.value))


def test_drain_turn_empty_text_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """agentMessage が無い（空応答）なら CodexEngineError。"""
    monkeypatch.setattr(codex_engine, "_record_usage", lambda *a, **k: None)
    events = [("turn/completed", {"turn": {"status": "completed"}})]
    server = _server_with_events(events)
    with pytest.raises(CodexEngineError):
        _run(server._drain_turn("th-1", source="chat"))


def test_drain_turn_eof_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """ターン中のプロセス断（__eof__）は CodexEngineError。"""
    monkeypatch.setattr(codex_engine, "_record_usage", lambda *a, **k: None)
    server = _server_with_events([("__eof__", {})])
    with pytest.raises(CodexEngineError):
        _run(server._drain_turn("th-1", source="chat"))


# --- run_turn / generate_once（_AppServer.run への委譲）---------------------


def test_run_turn_delegates_with_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """run_turn は with_tools=True で _server.run に委譲し (text, tool_runs) を返す。"""
    captured: dict[str, Any] = {}

    async def _fake_run(*, base: str, developer: str, prompt: str, with_tools: bool, source: str):
        captured.update(
            base=base, developer=developer, prompt=prompt, with_tools=with_tools, source=source
        )
        return "応答", [{"name": "submit_journal", "args": {"observations": "所見"}}]

    monkeypatch.setattr(codex_engine._server, "run", _fake_run)
    messages = [
        {"role": "system", "content": "CORE"},
        {"role": "user", "content": "夜の分析"},
    ]
    text, tool_runs = _run(codex_engine.run_turn(messages, phase=3, source="nightly"))
    assert text == "応答"
    assert tool_runs == [{"name": "submit_journal", "args": {"observations": "所見"}}]
    assert captured["with_tools"] is True
    assert captured["source"] == "nightly"
    assert captured["base"] == "CORE"


def test_read_loop_eof_old_generation_does_not_clobber() -> None:
    """旧世代 reader の EOF は新世代の _pending を消さない（再起動の世代競合・実機で踏んだ）。"""

    class _FakeStdout:
        async def readline(self) -> bytes:
            return b""  # 即 EOF（プロセス死亡を模す）

    class _FakeProc:
        stdout = _FakeStdout()

    server = _AppServer()
    old_proc = _FakeProc()
    new_proc = _FakeProc()
    # 「再 spawn 済み」＝現役は new_proc。旧世代 reader（old_proc）が EOF を踏む状況。
    server._proc = new_proc  # type: ignore[assignment]
    loop = asyncio.new_event_loop()
    fut: asyncio.Future[dict[str, object]] = loop.create_future()
    server._pending[99] = fut  # 新世代の handshake 相当
    try:
        loop.run_until_complete(server._read_loop(old_proc))  # type: ignore[arg-type]
    finally:
        loop.close()
    # 旧世代なので新世代の pending を消さない＝future は未解決のまま残る。
    assert 99 in server._pending
    assert not fut.done()


def test_generate_once_delegates_without_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """generate_once は with_tools=False で委譲し最終テキストだけ返す（dossier）。"""

    async def _fake_run(*, base: str, developer: str, prompt: str, with_tools: bool, source: str):
        assert with_tools is False
        return "要約JSON", []

    monkeypatch.setattr(codex_engine._server, "run", _fake_run)
    messages = [
        {"role": "system", "content": "要約指示"},
        {"role": "user", "content": "{...payload...}"},
    ]
    text = _run(codex_engine.generate_once(messages, source="dossier"))
    assert text == "要約JSON"
