"""send_once の冪等性（spec §3・§7・ADR-002/018）— 二重送信しないことの中核テスト。

同一 notify_key で 2 回呼んでも DiscordAdapter.send は 1 回しか走らない（2 回目は既送スキップ）。
DiscordAdapter.send をモックして呼び出し回数を assert する（実 Webhook は叩かない）。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.batch import notify
from app.db import repo
from app.db.engine import get_engine


def test_send_once_is_idempotent(monkeypatch: pytest.MonkeyPatch, temp_db: None) -> None:
    sent: list[str] = []

    def _fake_send(self: Any, content: str, *, embeds: Any = None) -> bool:
        sent.append(content)
        return True

    monkeypatch.setattr("app.adapters.discord.DiscordAdapter.send", _fake_send)

    key = "digest:2026-06-01"
    first = notify.send_once(key, "本日のサマリ")
    second = notify.send_once(key, "本日のサマリ（再実行）")

    assert first is True  # 1 回目は送る
    assert second is False  # 2 回目は既送でスキップ
    assert sent == ["本日のサマリ"]  # send は 1 回だけ

    with get_engine().connect() as conn:
        assert repo.notification_exists(conn, key, "discord") is True


def test_send_once_failure_not_recorded(monkeypatch: pytest.MonkeyPatch, temp_db: None) -> None:
    """送信失敗（False）は記録しない → 翌実行で再試行できる（at-least-once）。"""
    monkeypatch.setattr(
        "app.adapters.discord.DiscordAdapter.send",
        lambda self, content, *, embeds=None: False,
    )
    key = "digest:2026-06-02"
    assert notify.send_once(key, "失敗するはず") is False
    with get_engine().connect() as conn:
        assert repo.notification_exists(conn, key, "discord") is False  # 記録されていない
