"""notify_cost_warn の発火条件と冪等送信（ADR-028・spec §7.1）。

一時 SQLite の llm_usage に当月コストをスタブし、warn 超過時のみ 1 通送ること・冪等（月内 2 通目
は送らない）・未超過/off/block で no-op・例外時 JobResult(ok=False) を検証。Webhook は不使用。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from app.batch import notify
from app.batch.jobs import notify_cost_warn
from app.config import settings
from app.db import repo
from app.db.engine import get_engine


def _month() -> str:
    """当月（'YYYY-MM'・UTC）。ジョブと同じ算式でテストの境界を合わせる。"""
    return datetime.now(UTC).strftime("%Y-%m")


def _add_cost(cost_usd: float) -> None:
    """当月の llm_usage に 1 行積む（created_at は当月 ISO で先頭 7 文字がマッチする）。"""
    with get_engine().begin() as conn:
        repo.insert_llm_usage(
            conn,
            created_at=f"{_month()}-15T12:00:00+00:00",
            source="chat",
            model="test-model",
            tokens_in=1000,
            tokens_out=500,
            cost_usd=cost_usd,
        )


def test_run_sends_warn_when_exceeded(monkeypatch: pytest.MonkeyPatch, temp_db: None) -> None:
    """warn かつ当月累計 >= 上限 → llm_cost_warn:<月> で 1 通送り JobResult(ok=True, rows=1)。"""
    monkeypatch.setattr(settings, "llm_cost_guard_mode", "warn")
    monkeypatch.setattr(settings, "llm_cost_limit_usd", 1.0)
    _add_cost(0.8)
    _add_cost(0.5)  # 当月累計 1.3 >= 1.0

    sent: list[tuple[str, str]] = []

    def _fake_send_once(notify_key: str, content: str, **_: Any) -> bool:
        sent.append((notify_key, content))
        return True

    monkeypatch.setattr(notify_cost_warn.notify, "send_once", _fake_send_once)

    result = notify_cost_warn.run()
    assert result.ok is True
    assert result.rows == 1
    assert len(sent) == 1
    assert sent[0][0] == f"llm_cost_warn:{_month()}"
    assert "$1.30" in sent[0][1] and "$1.00" in sent[0][1]


def test_run_is_idempotent_within_month(monkeypatch: pytest.MonkeyPatch, temp_db: None) -> None:
    """本物の send_once を通すと、月内 2 通目は notification_exists で抑止され rows==0。"""
    monkeypatch.setattr(settings, "llm_cost_guard_mode", "warn")
    monkeypatch.setattr(settings, "llm_cost_limit_usd", 1.0)
    _add_cost(2.0)

    class _FakeAdapter:
        def send(self, content: str, *, embeds: Any = None) -> bool:
            return True

    # notify モジュール内の DiscordAdapter を fake に差し替え、send_once 本体（冪等）は本物で通す。
    monkeypatch.setattr(notify, "DiscordAdapter", _FakeAdapter)

    first = notify_cost_warn.run()
    assert first.ok is True and first.rows == 1

    second = notify_cost_warn.run()
    assert second.ok is True and second.rows == 0  # 既送のため冪等で送らない


def test_run_noop_when_below_limit(monkeypatch: pytest.MonkeyPatch, temp_db: None) -> None:
    """当月累計 < 上限 → 送信なし（rows==0・send_once 未呼び出し）。"""
    monkeypatch.setattr(settings, "llm_cost_guard_mode", "warn")
    monkeypatch.setattr(settings, "llm_cost_limit_usd", 50.0)
    _add_cost(1.0)

    sent: list[str] = []
    monkeypatch.setattr(
        notify_cost_warn.notify, "send_once", lambda k, c, **_: sent.append(k) or True
    )

    result = notify_cost_warn.run()
    assert result.ok is True
    assert result.rows == 0
    assert sent == []


def test_run_noop_when_usage_empty(monkeypatch: pytest.MonkeyPatch, temp_db: None) -> None:
    """llm_usage が空の月 → coalesce 0.0 で未超過 → no-op。"""
    monkeypatch.setattr(settings, "llm_cost_guard_mode", "warn")
    monkeypatch.setattr(settings, "llm_cost_limit_usd", 50.0)

    sent: list[str] = []
    monkeypatch.setattr(
        notify_cost_warn.notify, "send_once", lambda k, c, **_: sent.append(k) or True
    )

    result = notify_cost_warn.run()
    assert result.rows == 0
    assert sent == []


@pytest.mark.parametrize("mode", ["off", "block"])
def test_run_noop_when_mode_not_warn(
    mode: str, monkeypatch: pytest.MonkeyPatch, temp_db: None
) -> None:
    """off/block は超過していても送らない（warn 限定・block は別経路に任せる）。"""
    monkeypatch.setattr(settings, "llm_cost_guard_mode", mode)
    monkeypatch.setattr(settings, "llm_cost_limit_usd", 1.0)
    _add_cost(5.0)  # 超過しているが mode が warn でない

    sent: list[str] = []
    monkeypatch.setattr(
        notify_cost_warn.notify, "send_once", lambda k, c, **_: sent.append(k) or True
    )

    result = notify_cost_warn.run()
    assert result.ok is True
    assert result.rows == 0
    assert sent == []


def test_run_catches_exception(monkeypatch: pytest.MonkeyPatch, temp_db: None) -> None:
    """集計で例外 → JobResult(ok=False)（runner が error 通知）。"""

    def _boom(*_: Any, **__: Any) -> float:
        raise RuntimeError("DB 障害")

    monkeypatch.setattr(settings, "llm_cost_guard_mode", "warn")
    monkeypatch.setattr(notify_cost_warn.repo, "sum_llm_cost_month", _boom)

    result = notify_cost_warn.run()
    assert result.ok is False
    assert "DB 障害" in result.detail
