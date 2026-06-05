"""DiscordAdapter の送信契約（spec §4・ADR-018）。

実 Webhook は叩かず httpx.post をモックする。2xx→True／4xx・5xx・接続エラー→False（例外を
投げない）／webhook 未設定→no-op で False を検証する。
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.adapters.discord import DiscordAdapter


class _Resp:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


def test_send_2xx_returns_true(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(url: str, **kwargs: Any) -> _Resp:
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        return _Resp(204)

    monkeypatch.setattr(httpx, "post", _fake_post)
    ok = DiscordAdapter("https://discord.test/webhook").send("hello", embeds=[{"title": "x"}])
    assert ok is True
    assert captured["url"] == "https://discord.test/webhook"
    assert captured["json"]["content"] == "hello"
    assert captured["json"]["embeds"] == [{"title": "x"}]


@pytest.mark.parametrize("status", [400, 404, 500, 503])
def test_send_4xx_5xx_returns_false(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(status, "boom"))
    assert DiscordAdapter("https://discord.test/webhook").send("hello") is False


def test_send_connection_error_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*a: Any, **k: Any) -> _Resp:
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(httpx, "post", _raise)
    # 例外を投げず False（ADR-018）。
    assert DiscordAdapter("https://discord.test/webhook").send("hello") is False


def test_send_no_webhook_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"n": 0}

    def _fake_post(*a: Any, **k: Any) -> _Resp:
        called["n"] += 1
        return _Resp(204)

    monkeypatch.setattr(httpx, "post", _fake_post)
    adapter = DiscordAdapter("")  # 未設定
    assert adapter.enabled is False
    assert adapter.send("hello") is False
    assert called["n"] == 0  # POST は呼ばれない（no-op）
