"""軸1 夜の分析AI のテスト（phase3-spec.md §5・§10・ADR-018）。

LLM（complete）・Discord（notify.error）は必ずモック、DB は temp_db。検証対象:
- LLM 失敗時に journal をスキップして notify.error が呼ばれること。
- submit_journal 経由で observations/proposal が記録され、proposed_policy_change があれば
  proposal が起票されること。
- collect_situation_briefing が監査用 dict を返すこと（handler はモック）。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.advisor import nightly
from app.advisor.tools import handlers
from app.db import repo
from app.db.engine import get_engine


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _stub_briefing(monkeypatch: pytest.MonkeyPatch) -> None:
    """briefing 内の事実取得 handler を軽い dict 返しに差し替える（事実取得を切り離す）。"""

    async def _fake_signals(_args: dict[str, object]) -> dict[str, object]:
        return {"date": "2025-01-01", "signals": []}

    async def _fake_metrics(_args: dict[str, object]) -> dict[str, object]:
        return {"portfolio_id": 1, "sharpe": 1.2}

    async def _fake_overview(_args: dict[str, object]) -> dict[str, object]:
        return {"total_value": 1000.0}

    monkeypatch.setattr(handlers, "handle_get_signals", _fake_signals)
    monkeypatch.setattr(handlers, "handle_get_portfolio_metrics", _fake_metrics)
    monkeypatch.setattr(handlers, "handle_get_asset_overview", _fake_overview)


def test_collect_situation_briefing(monkeypatch: pytest.MonkeyPatch, temp_db: None) -> None:
    """briefing は signals/portfolio_metrics/asset_overview を集約した dict。"""
    _stub_briefing(monkeypatch)
    with get_engine().connect() as conn:
        briefing = nightly.collect_situation_briefing(conn)
    assert set(briefing) == {"signals", "portfolio_metrics", "asset_overview"}
    overview = briefing["asset_overview"]
    assert isinstance(overview, dict)
    assert overview["total_value"] == 1000.0


def test_nightly_records_journal_and_proposal(
    monkeypatch: pytest.MonkeyPatch, temp_db: None
) -> None:
    """submit_journal 経由で journal が残り、proposed_policy_change で proposal が起票される。"""
    _stub_briefing(monkeypatch)

    # run_tool_loop をモックして submit_journal の tool_run を返させる。
    submit_args = {
        "observations": "今日の所見",
        "proposal": "現金比率を上げる",
        "proposed_policy_change": {
            "field": "target_cash_ratio",
            "from": 0.25,
            "to": 0.4,
            "reason": "下落に備える",
        },
    }

    async def _fake_loop(messages: Any, **_: Any) -> tuple[str, list[dict[str, object]]]:
        return "最終応答", [{"name": "submit_journal", "args": submit_args}]

    monkeypatch.setattr(nightly, "run_tool_loop", _fake_loop)

    with get_engine().begin() as conn:
        _run(nightly.run_nightly_advisor(conn))

    with get_engine().connect() as conn:
        journals = repo.list_journal(conn)
        proposals = repo.list_proposals(conn)
        detail = repo.get_journal(conn, journals[0]["id"])
    assert detail is not None

    assert len(journals) == 1
    j = journals[0]
    assert j["source"] == "nightly"
    assert j["observations"] == "今日の所見"
    assert j["proposal"] == "現金比率を上げる"
    assert json.loads(j["proposed_policy_change"])["to"] == 0.4
    # situation_briefing は詳細取得でのみ載る（監査用 JSON）。
    assert json.loads(detail["situation_briefing"])["asset_overview"]["total_value"] == 1000.0

    # proposed_policy_change があるので proposal（policy_change・pending）が 1 件起票される。
    assert len(proposals) == 1
    p = proposals[0]
    assert p["kind"] == "policy_change"
    assert p["status"] == "pending"
    assert p["journal_id"] == j["id"]
    assert json.loads(p["body"])["field"] == "target_cash_ratio"


def test_nightly_without_policy_change_no_proposal(
    monkeypatch: pytest.MonkeyPatch, temp_db: None
) -> None:
    """proposed_policy_change が無ければ journal だけ残り proposal は起票しない。"""
    _stub_briefing(monkeypatch)

    async def _fake_loop(messages: Any, **_: Any) -> tuple[str, list[dict[str, object]]]:
        return "応答", [{"name": "submit_journal", "args": {"observations": "所見のみ"}}]

    monkeypatch.setattr(nightly, "run_tool_loop", _fake_loop)

    with get_engine().begin() as conn:
        _run(nightly.run_nightly_advisor(conn))

    with get_engine().connect() as conn:
        assert len(repo.list_journal(conn)) == 1
        assert repo.list_proposals(conn) == []


def test_nightly_non_dict_policy_change_keeps_journal_no_proposal(
    monkeypatch: pytest.MonkeyPatch, temp_db: None
) -> None:
    """proposed_policy_change が非 dict（文字列）でも journal は残り proposal は起票しない。

    非力なモデルが変更案を markdown 文字列で渡すケースの回帰（頑健性・ADR-018）。
    """
    _stub_briefing(monkeypatch)

    async def _fake_loop(messages: Any, **_: Any) -> tuple[str, list[dict[str, object]]]:
        return "応答", [
            {
                "name": "submit_journal",
                "args": {
                    "observations": "所見",
                    "proposal": "提案",
                    "proposed_policy_change": "- 文字列で来た変更案",
                },
            }
        ]

    monkeypatch.setattr(nightly, "run_tool_loop", _fake_loop)

    with get_engine().begin() as conn:
        _run(nightly.run_nightly_advisor(conn))

    with get_engine().connect() as conn:
        journals = repo.list_journal(conn)
        assert len(journals) == 1
        assert journals[0]["observations"] == "所見"
        assert journals[0]["proposal"] == "提案"
        assert journals[0]["proposed_policy_change"] is None
        assert repo.list_proposals(conn) == []


def test_nightly_multi_field_patch_keeps_journal_no_proposal(
    monkeypatch: pytest.MonkeyPatch, temp_db: None
) -> None:
    """多フィールド patch でも journal は残り、適用不能 proposal は起票しない（U-10 裁定①）。

    弱モデルが複数列同時変更（{field,to} 単一形でない）を渡しても、coerce_policy_change が
    None に倒すため proposal queue に適用不能な提案が入らないことを担保する（ADR-013/018）。
    """
    _stub_briefing(monkeypatch)

    async def _fake_loop(messages: Any, **_: Any) -> tuple[str, list[dict[str, object]]]:
        return "応答", [
            {
                "name": "submit_journal",
                "args": {
                    "observations": "所見",
                    "proposal": "提案",
                    "proposed_policy_change": {
                        "max_position_weight": 0.2,
                        "target_cash_ratio": 0.4,
                    },
                },
            }
        ]

    monkeypatch.setattr(nightly, "run_tool_loop", _fake_loop)

    with get_engine().begin() as conn:
        _run(nightly.run_nightly_advisor(conn))

    with get_engine().connect() as conn:
        journals = repo.list_journal(conn)
        assert len(journals) == 1
        assert journals[0]["observations"] == "所見"
        assert journals[0]["proposed_policy_change"] is None
        assert repo.list_proposals(conn) == []


def test_nightly_llm_failure_skips_journal_and_notifies(
    monkeypatch: pytest.MonkeyPatch, temp_db: None
) -> None:
    """LLM 失敗時は journal をスキップし notify.error を呼ぶ（ADR-018）。"""
    _stub_briefing(monkeypatch)

    async def _failing_loop(messages: Any, **_: Any) -> tuple[str, list[dict[str, object]]]:
        raise RuntimeError("LLM タイムアウト")

    monkeypatch.setattr(nightly, "run_tool_loop", _failing_loop)

    notified: dict[str, Any] = {}

    def _fake_error(title: str, detail: str) -> None:
        notified["title"] = title
        notified["detail"] = detail

    monkeypatch.setattr(nightly.notify, "error", _fake_error)

    with get_engine().begin() as conn:
        _run(nightly.run_nightly_advisor(conn))

    with get_engine().connect() as conn:
        # journal は欠かす（スキップ）。
        assert repo.list_journal(conn) == []
    assert notified["title"] == "夜の分析AI 失敗"
    assert "LLM タイムアウト" in notified["detail"]


def test_run_advisor_job_returns_jobresult(monkeypatch: pytest.MonkeyPatch, temp_db: None) -> None:
    """run_advisor.run は同期で JobResult(ok=True) を返す（async を asyncio.run で駆動）。"""
    from app.batch.jobs import run_advisor

    async def _noop(conn: Any) -> None:
        return None

    monkeypatch.setattr(run_advisor, "run_nightly_advisor", _noop)
    result = run_advisor.run()
    assert result.name == "run_advisor"
    assert result.ok is True
