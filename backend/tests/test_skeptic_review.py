"""提案前 red-team 反証（skeptic 面・red_team_proposals）のテスト（ADR-086）。

LLM（run_turn）は必ずモック、DB は temp_db（本物 DB に触れない）。当夜 pending の buy/sell 提案で
ゲートを駆動する。検証:
- ゲート＝未反証の pending 提案が無ければ run_turn を呼ばず skip／あれば発火。
- run_turn に SKEPTIC_TOOLSET＋source='skeptic' が渡る（toolset 制限）。
- submit_refutation が body.skeptic に注記され、status は pending のまま（自動却下しない）。
- LLM ハード失敗は握らず伝播し、body は無改変（中途注記なし）。
- 二度目は反証済みを新着に数えない（body.skeptic の有無で有界化・冪等）。
- allowed_ids 外/幻覚 proposal_id への submit_refutation は drop。
- ジョブ＝skip/発火/面未設定は ok=True・ハード失敗は ok=False・NIGHTLY_JOBS の順序・digest 行。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.advisor import skeptic
from app.advisor.tools.registry import SKEPTIC_TOOLSET
from app.db import repo
from app.db.engine import get_engine


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _seed_buy_proposal(conn: Any, *, code: str = "72030", date: str = "2026-02-01") -> int:
    """pending の buy 提案を 1 件起票し id を返す（body に判断属性込み・ADR-084）。"""
    body = json.dumps(
        {
            "code": code,
            "company_name": "銘柄",
            "market": "JP",
            "conviction": "high",
            "catalyst": "決算",
            "invalidation": "ガイダンス下方修正",
        },
        ensure_ascii=False,
    )
    return repo.insert_proposal(
        conn, created_date=date, kind="buy", body=body, rationale="好決算期待", status="pending"
    )


def _record_run_turn(monkeypatch: pytest.MonkeyPatch, tool_runs: list[dict[str, Any]]):
    """skeptic.run_turn を差し替え、呼び出し記録と返す tool_runs を制御する。"""
    calls: list[dict[str, Any]] = []

    async def _fake(messages: Any, **kwargs: Any) -> tuple[str, list[dict[str, Any]]]:
        calls.append(kwargs)
        return "反証所見", tool_runs

    monkeypatch.setattr(skeptic, "run_turn", _fake)
    return calls


# ===== ゲート =====


def test_gate_no_pending_skips_without_llm(monkeypatch: pytest.MonkeyPatch, temp_db: None):
    """未反証の pending 提案が無ければ run_turn を呼ばず skip する（ADR-086）。"""
    calls = _record_run_turn(monkeypatch, [])
    with get_engine().begin() as conn:
        result = _run(skeptic.run_skeptic_review(conn))

    assert result["ran"] is False
    assert "skip" in result["reason"]
    assert calls == []


def test_gate_fires_with_skeptic_toolset(monkeypatch: pytest.MonkeyPatch, temp_db: None):
    """pending の buy 提案があれば発火し、run_turn に SKEPTIC_TOOLSET＋source='skeptic' が渡る。"""
    calls = _record_run_turn(monkeypatch, [])
    with get_engine().begin() as conn:
        _seed_buy_proposal(conn)

    with get_engine().begin() as conn:
        result = _run(skeptic.run_skeptic_review(conn))

    assert result["ran"] is True
    assert result["n_pending"] == 1
    assert len(calls) == 1
    assert calls[0]["tool_names"] == SKEPTIC_TOOLSET
    assert calls[0]["source"] == "skeptic"


# ===== persist（body.skeptic への注記） =====


def test_persist_bakes_skeptic_into_body(monkeypatch: pytest.MonkeyPatch, temp_db: None):
    """submit_refutation が body.skeptic に注記され、status は pending のまま（ADR-009）。"""
    with get_engine().begin() as conn:
        pid = _seed_buy_proposal(conn)

    _record_run_turn(
        monkeypatch,
        [
            {
                "name": "submit_refutation",
                "args": {"proposal_id": pid, "verdict": "fragile", "refutation": "需給悪化を無視"},
            }
        ],
    )
    with get_engine().begin() as conn:
        result = _run(skeptic.run_skeptic_review(conn))

    assert result["reviewed"] == [pid]
    with get_engine().connect() as conn:
        prop = repo.get_proposal(conn, pid)
    assert prop is not None
    body = json.loads(prop["body"])
    assert body["skeptic"]["refutation"] == "需給悪化を無視"
    assert body["skeptic"]["verdict"] == "fragile"
    assert prop["status"] == "pending"  # 反証は注記だけ＝自動却下しない


def test_llm_failure_propagates_and_body_unchanged(monkeypatch: pytest.MonkeyPatch, temp_db: None):
    """LLM ハード失敗は握らず伝播し、body に skeptic は付かない（中途注記なし・ADR-018）。"""

    async def _boom(messages: Any, **_: Any) -> tuple[str, list[dict[str, Any]]]:
        raise RuntimeError("LLM 500")

    monkeypatch.setattr(skeptic, "run_turn", _boom)
    with get_engine().begin() as conn:
        pid = _seed_buy_proposal(conn)

    with pytest.raises(RuntimeError, match="LLM 500"):  # noqa: PT012 — begin 内で発火を確認
        with get_engine().begin() as conn:
            _run(skeptic.run_skeptic_review(conn))

    with get_engine().connect() as conn:
        prop = repo.get_proposal(conn, pid)
    assert "skeptic" not in json.loads(prop["body"])


def test_second_run_skips_reviewed(monkeypatch: pytest.MonkeyPatch, temp_db: None):
    """一度反証注記した提案は次回ゲートの新着に数えない（body.skeptic の有無で有界化）。"""
    with get_engine().begin() as conn:
        pid = _seed_buy_proposal(conn)

    _record_run_turn(
        monkeypatch,
        [
            {
                "name": "submit_refutation",
                "args": {"proposal_id": pid, "verdict": "weak", "refutation": "弱い"},
            }
        ],
    )
    with get_engine().begin() as conn:
        first = _run(skeptic.run_skeptic_review(conn))
    assert first["ran"] is True

    with get_engine().begin() as conn:
        second = _run(skeptic.run_skeptic_review(conn))
    assert second["ran"] is False
    assert second["n_pending"] == 0


def test_guard_drops_unknown_proposal_id(monkeypatch: pytest.MonkeyPatch, temp_db: None):
    """allowed_ids 外/幻覚 proposal_id への submit_refutation は drop する（多重防御・ADR-086）。"""
    with get_engine().begin() as conn:
        pid = _seed_buy_proposal(conn)

    _record_run_turn(
        monkeypatch,
        [
            {
                "name": "submit_refutation",
                "args": {"proposal_id": 999999, "verdict": "weak", "refutation": "x"},
            }
        ],
    )
    with get_engine().begin() as conn:
        result = _run(skeptic.run_skeptic_review(conn))

    assert result["reviewed"] == []
    with get_engine().connect() as conn:
        prop = repo.get_proposal(conn, pid)
    assert "skeptic" not in json.loads(prop["body"])


# ===== ジョブ =====


def test_job_skip_returns_ok_true(monkeypatch: pytest.MonkeyPatch, temp_db: None):
    """ゲート skip は JobResult(ok=True, rows=0)（健全 no-op・ADR-018）。"""
    from app.batch.jobs import red_team_proposals

    _record_run_turn(monkeypatch, [])  # pending 無し → skip
    result = red_team_proposals.run()
    assert result.ok is True
    assert result.rows == 0


def test_job_fire_returns_ok_true_with_rows(monkeypatch: pytest.MonkeyPatch, temp_db: None):
    """発火して反証を注記すれば JobResult(ok=True, rows=1)。"""
    from app.batch.jobs import red_team_proposals

    with get_engine().begin() as conn:
        pid = _seed_buy_proposal(conn)
    _record_run_turn(
        monkeypatch,
        [
            {
                "name": "submit_refutation",
                "args": {"proposal_id": pid, "verdict": "holds", "refutation": "筋は通る"},
            }
        ],
    )

    result = red_team_proposals.run()
    assert result.ok is True
    assert result.rows == 1


def test_job_face_unconfigured_is_silent_skip(monkeypatch: pytest.MonkeyPatch, temp_db: None):
    """skeptic 面 未設定は沈黙 skip（ok=True・nag しない・ADR-018/058）。"""
    from app.batch.jobs import red_team_proposals
    from app.services.llm_config import FaceNotConfiguredError

    async def _raise_face(conn: Any) -> dict[str, object]:
        raise FaceNotConfiguredError("skeptic 未設定")

    monkeypatch.setattr(red_team_proposals, "run_skeptic_review", _raise_face)
    result = red_team_proposals.run()
    assert result.ok is True
    assert result.rows == 0
    assert "未設定" in result.detail


def test_job_hard_failure_returns_ok_false(monkeypatch: pytest.MonkeyPatch, temp_db: None):
    """設定済みで落ちる（LLM/DB 障害）は ok=False で surface（runner 集約通知・ADR-018）。"""
    from app.batch.jobs import red_team_proposals

    async def _boom(conn: Any) -> dict[str, object]:
        raise RuntimeError("LLM 500")

    monkeypatch.setattr(red_team_proposals, "run_skeptic_review", _boom)
    result = red_team_proposals.run()
    assert result.ok is False
    assert "LLM 500" in result.detail


def test_nightly_jobs_order_skeptic_after_advisor_before_notify():
    """NIGHTLY_JOBS で run_advisor < red_team_proposals < notify_digest（ADR-086）。"""
    from app.batch.jobs import NIGHTLY_JOBS

    mods = [f.__module__ for f in NIGHTLY_JOBS]
    i_advisor = mods.index("app.batch.jobs.run_advisor")
    i_skeptic = mods.index("app.batch.jobs.red_team_proposals")
    i_notify = mods.index("app.batch.jobs.notify_digest")
    assert i_advisor < i_skeptic < i_notify


# ===== digest 行 =====


def test_digest_shows_skeptic_line(temp_db: None):
    """当夜 skeptic が注記した反証があれば digest に 🧠 の 1 行が出る（ADR-086）。"""
    from datetime import UTC, datetime

    from app.batch.jobs.notify_digest import build_digest_content

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    body = json.dumps(
        {
            "code": "72030",
            "company_name": "銘柄",
            "market": "JP",
            "skeptic": {"verdict": "weak", "refutation": "需給悪化", "reviewed_at": today},
        },
        ensure_ascii=False,
    )
    with get_engine().begin() as conn:
        repo.insert_proposal(
            conn, created_date=today, kind="buy", body=body, rationale="根拠", status="pending"
        )

    with get_engine().connect() as conn:
        content = build_digest_content(conn, today)
    assert content is not None
    assert "🧠" in content
    assert "/proposals" in content
