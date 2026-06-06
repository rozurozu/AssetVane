"""Discord 疎通テストの脳と口（send_test_notification / POST /diagnostics/discord-test）。

担保: 冪等を通さず毎回 DiscordAdapter.send を呼ぶこと、未設定（enabled=False）と送信失敗
（sent=False）を区別して返すこと。実 Webhook は叩かず httpx.post をモックする
（ADR-007/011/018・[[testing-strategy]]）。エンドポイントは未設定でも 200 で結果フラグを返す。
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.batch import notify
from app.config import settings
from app.main import app


class _Resp:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


def test_enabled_and_sent_on_2xx(monkeypatch: pytest.MonkeyPatch) -> None:
    """Webhook 設定済み＋2xx で enabled=True・sent=True、かつ実際に POST が飛ぶ。"""
    calls = {"n": 0}

    def _fake_post(*a: Any, **k: Any) -> _Resp:
        calls["n"] += 1
        return _Resp(204)

    monkeypatch.setattr(settings, "discord_webhook_url", "https://discord.test/webhook")
    monkeypatch.setattr(httpx, "post", _fake_post)

    result = notify.send_test_notification()
    assert result.enabled is True
    assert result.sent is True
    assert calls["n"] == 1  # 冪等を通さず必ず 1 回飛ぶ


def test_not_enabled_when_webhook_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Webhook 未設定なら enabled=False・sent=False で POST は飛ばない（no-op）。"""
    calls = {"n": 0}

    def _fake_post(*a: Any, **k: Any) -> _Resp:
        calls["n"] += 1
        return _Resp(204)

    monkeypatch.setattr(settings, "discord_webhook_url", "")
    monkeypatch.setattr(httpx, "post", _fake_post)

    result = notify.send_test_notification()
    assert result.enabled is False
    assert result.sent is False
    assert calls["n"] == 0


def test_enabled_but_send_fails_on_5xx(monkeypatch: pytest.MonkeyPatch) -> None:
    """設定済みでも 5xx なら enabled=True・sent=False（未設定と送信失敗を区別）。"""
    monkeypatch.setattr(settings, "discord_webhook_url", "https://discord.test/webhook")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(503, "boom"))

    result = notify.send_test_notification()
    assert result.enabled is True
    assert result.sent is False


def test_endpoint_returns_result_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /diagnostics/discord-test は未設定でも 200 で {enabled,sent} を返す。"""
    monkeypatch.setattr(settings, "discord_webhook_url", "")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(204))

    with TestClient(app) as client:
        resp = client.post("/diagnostics/discord-test")
    assert resp.status_code == 200
    assert resp.json() == {"enabled": False, "sent": False}
