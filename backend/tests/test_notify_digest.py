"""notify_digest の digest 組み立てと送信（phase6-spec.md §3）。

一時 SQLite に signals / advisor_journal / policy をスタブし、⑧抽出（score 閾値・notable）・
Top N 切り詰め・⑦リバランス判定・当日提案の取り込み・ALWAYS_DAILY_DIGEST=False のスキップ・
例外時 JobResult(ok=False) を検証する。実 Webhook は叩かない。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.batch.jobs import notify_digest
from app.config import settings
from app.db import repo
from app.db.engine import get_engine

STOCK = {
    "code": "72030",
    "company_name": "トヨタ自動車",
    "sector33_code": "3700",
    "sector17_code": "6",
    "market_code": "0111",
    "is_etf": 0,
    "updated_at": "2026-06-02T00:00:00+00:00",
}
STOCK2 = {**STOCK, "code": "67580", "company_name": "ソニーグループ"}


def _signal(code: str, signal_type: str, score: float, payload: dict[str, Any], date: str) -> dict:
    return {
        "date": date,
        "code": code,
        "signal_type": signal_type,
        "score": score,
        "payload": json.dumps(payload, ensure_ascii=False),
    }


def test_build_digest_extracts_alerts_and_proposal(temp_db: None) -> None:
    repo.upsert_stocks([STOCK, STOCK2])
    sig_date = "2026-03-01"
    repo.upsert_signals(
        [
            # 高スコア → アラート。
            _signal("72030", "momentum", 0.75, {"label": "GC", "notable": True}, sig_date),
            # 低スコアだが notable（出来高 3 倍）→ アラート。
            _signal(
                "67580", "volume_spike", 0.35, {"label": "出来高3.5倍", "notable": True}, sig_date
            ),
            # 低スコア・非 notable → 対象外。
            _signal("72030", "volume_spike", 0.2, {"label": "微増", "notable": False}, sig_date),
        ]
    )
    with get_engine().begin() as conn:
        repo.insert_journal(
            conn,
            date="2026-06-05",
            source="nightly",
            proposal="現金比率を上げる検討",
            proposed_policy_change=json.dumps({"field": "target_cash_ratio", "to": 0.2}),
        )

    with get_engine().connect() as conn:
        content = notify_digest.build_digest_content(conn, "2026-06-05")

    assert content is not None
    assert "トヨタ自動車 (72030)" in content
    assert "ソニーグループ (67580)" in content
    assert "微増" not in content  # 非 notable・低スコアは載らない
    assert "現金比率を上げる検討" in content
    assert "target_cash_ratio → 0.2" in content
    assert "注目 2 件" in content


def test_build_digest_top_n_truncates(monkeypatch: pytest.MonkeyPatch, temp_db: None) -> None:
    monkeypatch.setattr(settings, "alert_top_n", 2)
    repo.upsert_stocks([STOCK])
    sig_date = "2026-03-01"
    repo.upsert_signals(
        [
            _signal(
                "72030",
                f"momentum{i}",
                0.9 - i * 0.1,
                {"label": f"L{i}", "notable": True},
                sig_date,
            )
            for i in range(5)
        ]
    )
    with get_engine().connect() as conn:
        content = notify_digest.build_digest_content(conn, "2026-06-05")
    assert content is not None
    assert "…ほか 3 件" in content  # 5 件中 Top 2 表示・残り 3


def test_build_digest_rebalance_alert(temp_db: None) -> None:
    """policy.updated_at が rebalance_alert_days 超ならリバランス行が出る。"""
    old = (datetime.now(UTC) - timedelta(days=settings.rebalance_alert_days + 5)).isoformat()
    with get_engine().begin() as conn:
        repo.upsert_policy(conn, {"risk_tolerance": "中", "updated_at": old})

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    with get_engine().connect() as conn:
        content = notify_digest.build_digest_content(conn, today)
    assert content is not None
    assert "リバランス" in content
    assert "方針を見直す時期" in content


def test_build_digest_skips_when_empty_and_not_always(
    monkeypatch: pytest.MonkeyPatch, temp_db: None
) -> None:
    """ALWAYS_DAILY_DIGEST=False かつ⑦⑧・提案すべて無し → None（送らない）。"""
    monkeypatch.setattr(settings, "always_daily_digest", False)
    with get_engine().connect() as conn:
        content = notify_digest.build_digest_content(conn, "2026-06-05")
    assert content is None


def test_build_digest_always_sends_summary_when_empty(
    monkeypatch: pytest.MonkeyPatch, temp_db: None
) -> None:
    """ALWAYS_DAILY_DIGEST=True（既定）なら検知ゼロでもサマリを返す（毎朝届く）。"""
    monkeypatch.setattr(settings, "always_daily_digest", True)
    with get_engine().connect() as conn:
        content = notify_digest.build_digest_content(conn, "2026-06-05")
    assert content is not None
    assert "注目シグナル: なし" in content


def test_run_sends_once_and_returns_jobresult(
    monkeypatch: pytest.MonkeyPatch, temp_db: None
) -> None:
    """run() は build → send_once で 1 通送り JobResult(ok=True) を返す。"""
    sent: list[tuple[str, str]] = []

    def _fake_send_once(notify_key: str, content: str, **_: Any) -> bool:
        sent.append((notify_key, content))
        return True

    monkeypatch.setattr(notify_digest.notify, "send_once", _fake_send_once)

    result = notify_digest.run()
    assert result.ok is True
    assert result.rows == 1
    assert len(sent) == 1
    assert sent[0][0].startswith("digest:")


def test_run_catches_exception(monkeypatch: pytest.MonkeyPatch, temp_db: None) -> None:
    """build で例外 → JobResult(ok=False)（runner が error 通知）。"""

    def _boom(conn: Any, today: str) -> None:
        raise RuntimeError("DB 障害")

    monkeypatch.setattr(notify_digest, "build_digest_content", _boom)
    result = notify_digest.run()
    assert result.ok is False
    assert "DB 障害" in result.detail
