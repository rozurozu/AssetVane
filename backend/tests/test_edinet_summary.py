"""edinet_summary.summarize_business_description の整形部を担保する（ADR-056/020/014）。

generate_once（LLM 単発）を monkeypatch し、要約ヘルパが「system に要約指示・user に本文・
source='dossier' で 1 回呼ぶ」「本文を _MAX_INPUT_CHARS で截断して渡す」を満たすことを検証する
（LLM 本体には触れない・testing-strategy「ネットに出ない」）。
"""

from __future__ import annotations

import asyncio

from app.advisor import edinet_summary


def _run(coro):  # noqa: ANN001, ANN202
    """async ヘルパを 1 回駆動する（新しいイベントループで回す・pytest-asyncio 不使用）。"""
    return asyncio.run(coro)


def test_summarize_passes_instruction_and_source(monkeypatch) -> None:
    """system に要約指示・user に本文・source='dossier' で generate_once を呼び結果を返す。"""
    from app.advisor import engine

    captured = {}

    async def _fake(messages, *, source):  # noqa: ANN001, ANN202
        captured["messages"] = messages
        captured["source"] = source
        return "要約結果"

    monkeypatch.setattr(engine, "generate_once", _fake)

    out = _run(edinet_summary.summarize_business_description("ABC 事業の本文。"))

    assert out == "要約結果"
    assert captured["source"] == "dossier"
    messages = captured["messages"]
    assert messages[0]["role"] == "system"
    assert "事業の内容" in messages[0]["content"]
    assert messages[1] == {"role": "user", "content": "ABC 事業の本文。"}


def test_summarize_truncates_input(monkeypatch) -> None:
    """本文は _MAX_INPUT_CHARS で截断して渡す（トークン暴走の保険・ADR-056）。"""
    from app.advisor import engine

    captured = {}

    async def _fake(messages, *, source):  # noqa: ANN001, ANN202
        captured["messages"] = messages
        return "x"

    monkeypatch.setattr(engine, "generate_once", _fake)

    long_text = "あ" * 20_000
    _run(edinet_summary.summarize_business_description(long_text))

    user_content = captured["messages"][1]["content"]
    assert len(user_content) == edinet_summary._MAX_INPUT_CHARS
