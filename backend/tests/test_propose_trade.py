"""ニュース起点 buy/sell 提案の起票テスト（ADR-052）。

LLM はモック、DB は temp_db/一時 SQLite。検証対象:
- resolve_trade_target が JP→US で解決し、未知コードは None。
- handle_propose_trade（検証 only）が銘柄解決して {ok, company_name, market}／未知は {error}。
- persist_trade_proposals_from_tool_runs：単一/複数起票・未知 drop・pending dedup・
  reject 済みは再起票可・journal_id 紐付け。
- 承認（resolve_proposal）で kind=buy/sell は policy 非適用・status 遷移のみ（提示専用・ADR-009）。
- /chat：propose_trade のみ（submit なし）で journal は書かず proposal だけ起票（ADR-029/052）。
- nightly：trade 起票が journal と同一トランザクションで束ねられる。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.advisor import journaling, nightly, service
from app.advisor.llm import LLMResponse, ToolCall
from app.advisor.tools import handlers
from app.db import repo
from app.db.engine import get_engine


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _seed_stocks() -> None:
    """JP 1 銘柄・US 1 銘柄をマスタに焼く（resolve_trade_target がヒットするように）。"""
    repo.upsert_stocks([{"code": "72030", "company_name": "トヨタ自動車"}])
    repo.upsert_us_stocks([{"symbol": "AAPL", "company_name": "Apple Inc."}])


# --- resolve_trade_target ---------------------------------------------------


def test_resolve_trade_target_jp_us_unknown(temp_db: None) -> None:
    _seed_stocks()
    with get_engine().connect() as conn:
        jp = journaling.resolve_trade_target(conn, "72030")
        us = journaling.resolve_trade_target(conn, "AAPL")
        unknown = journaling.resolve_trade_target(conn, "99999")
    assert jp == {"company_name": "トヨタ自動車", "market": "JP"}
    assert us == {"company_name": "Apple Inc.", "market": "US"}
    assert unknown is None


# --- handle_propose_trade（検証 only）---------------------------------------


def test_handle_propose_trade_jp_ok(temp_db: None) -> None:
    _seed_stocks()
    out = _run(
        handlers.handle_propose_trade({"action": "buy", "code": "72030", "reason": "好材料"})
    )
    assert out == {
        "ok": True,
        "action": "buy",
        "code": "72030",
        "company_name": "トヨタ自動車",
        "market": "JP",
    }


def test_handle_propose_trade_us_ok(temp_db: None) -> None:
    _seed_stocks()
    out = _run(
        handlers.handle_propose_trade({"action": "sell", "code": "AAPL", "reason": "悪材料"})
    )
    assert out["ok"] is True
    assert out["market"] == "US"
    assert out["company_name"] == "Apple Inc."


def test_handle_propose_trade_unknown_code_returns_error(temp_db: None) -> None:
    _seed_stocks()
    out = _run(handlers.handle_propose_trade({"action": "buy", "code": "00000", "reason": "x"}))
    assert "error" in out
    assert "ok" not in out


def test_handle_propose_trade_invalid_action_returns_error(temp_db: None) -> None:
    _seed_stocks()
    out = _run(handlers.handle_propose_trade({"action": "hold", "code": "72030", "reason": "x"}))
    assert "error" in out


def test_handle_propose_trade_db_error_returns_error(
    temp_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DB アクセス（銘柄解決）が例外でも {"error"} を返し例外を漏らさない。

    handlers 規約「例外は握って {"error"} を返す（dispatch ループを落とさない）」の検証
    （tasks/review-2026-06-12.md C-5）。
    """
    _seed_stocks()

    def _boom(conn: Any, code: str) -> dict[str, str] | None:
        raise RuntimeError("DB が壊れた")

    monkeypatch.setattr(handlers, "resolve_trade_target", _boom)
    out = _run(handlers.handle_propose_trade({"action": "buy", "code": "72030", "reason": "x"}))
    assert "error" in out
    assert "ok" not in out


# --- persist_trade_proposals_from_tool_runs --------------------------------


def _trade_run(action: str, code: str, reason: str) -> dict[str, Any]:
    return {"name": "propose_trade", "args": {"action": action, "code": code, "reason": reason}}


def test_persist_single_trade_proposal(temp_db: None) -> None:
    _seed_stocks()
    with get_engine().begin() as conn:
        ids = journaling.persist_trade_proposals_from_tool_runs(
            conn,
            tool_runs=[_trade_run("buy", "72030", "強い上方修正")],
            date="2026-06-11",
            journal_id=None,
        )
    assert len(ids) == 1
    with get_engine().connect() as conn:
        props = repo.list_proposals(conn)
    assert len(props) == 1
    p = props[0]
    assert p["kind"] == "buy"
    assert p["status"] == "pending"
    assert p["rationale"] == "強い上方修正"
    assert p["depends_on"] is None
    body = json.loads(p["body"])
    assert body == {"code": "72030", "company_name": "トヨタ自動車", "market": "JP"}


def test_persist_multiple_trade_proposals(temp_db: None) -> None:
    """1 ターンに複数の propose_trade があれば全件起票する（買い A・売り B）。"""
    _seed_stocks()
    with get_engine().begin() as conn:
        ids = journaling.persist_trade_proposals_from_tool_runs(
            conn,
            tool_runs=[_trade_run("buy", "72030", "好材料"), _trade_run("sell", "AAPL", "悪材料")],
            date="2026-06-11",
        )
    assert len(ids) == 2
    with get_engine().connect() as conn:
        kinds = {p["kind"] for p in repo.list_proposals(conn)}
    assert kinds == {"buy", "sell"}


def test_persist_drops_unknown_code(temp_db: None) -> None:
    """未知コードは起票せず drop（ADR-018＝queue に幻覚を入れない）。"""
    _seed_stocks()
    with get_engine().begin() as conn:
        ids = journaling.persist_trade_proposals_from_tool_runs(
            conn,
            tool_runs=[_trade_run("buy", "00000", "幻覚銘柄"), _trade_run("buy", "72030", "実在")],
            date="2026-06-11",
        )
    assert len(ids) == 1
    with get_engine().connect() as conn:
        props = repo.list_proposals(conn)
    assert len(props) == 1
    assert json.loads(props[0]["body"])["code"] == "72030"


def test_persist_dedup_pending_same_kind_code(temp_db: None) -> None:
    """同一 (kind, code) の pending があれば 2 回目はスキップ（重複起票防止）。"""
    _seed_stocks()
    with get_engine().begin() as conn:
        journaling.persist_trade_proposals_from_tool_runs(
            conn, tool_runs=[_trade_run("buy", "72030", "1 回目")], date="2026-06-11"
        )
    with get_engine().begin() as conn:
        ids = journaling.persist_trade_proposals_from_tool_runs(
            conn, tool_runs=[_trade_run("buy", "72030", "2 回目")], date="2026-06-12"
        )
    assert ids == []
    with get_engine().connect() as conn:
        props = repo.list_proposals(conn)
    assert len(props) == 1
    # 初回が保持される（rationale は 1 回目のまま）。
    assert props[0]["rationale"] == "1 回目"


def test_persist_rejected_allows_repropose(temp_db: None) -> None:
    """reject 済みは pending を塞がない＝状況変化後の再提案は通る。"""
    _seed_stocks()
    with get_engine().begin() as conn:
        ids = journaling.persist_trade_proposals_from_tool_runs(
            conn, tool_runs=[_trade_run("buy", "72030", "初回")], date="2026-06-11"
        )
        service.resolve_proposal(conn, ids[0], decision="rejected")
    with get_engine().begin() as conn:
        ids2 = journaling.persist_trade_proposals_from_tool_runs(
            conn, tool_runs=[_trade_run("buy", "72030", "再提案")], date="2026-06-12"
        )
    assert len(ids2) == 1
    with get_engine().connect() as conn:
        assert len(repo.list_proposals(conn)) == 2


def test_persist_links_journal_id(temp_db: None) -> None:
    _seed_stocks()
    with get_engine().begin() as conn:
        jid = repo.insert_journal(conn, date="2026-06-11", source="nightly", observations="所見")
        ids = journaling.persist_trade_proposals_from_tool_runs(
            conn, tool_runs=[_trade_run("buy", "72030", "根拠")], date="2026-06-11", journal_id=jid
        )
    with get_engine().connect() as conn:
        p = repo.get_proposal(conn, ids[0])
    assert p is not None
    assert p["journal_id"] == jid


# --- 承認は約定を起こさない（提示専用・ADR-009）-----------------------------


def test_approve_buy_does_not_change_policy(temp_db: None) -> None:
    """kind=buy の承認は status 遷移のみ＝policy は不変（policy_change と違い適用なし）。"""
    _seed_stocks()
    with get_engine().begin() as conn:
        before = repo.get_policy(conn)
        ids = journaling.persist_trade_proposals_from_tool_runs(
            conn, tool_runs=[_trade_run("buy", "72030", "根拠")], date="2026-06-11"
        )
        service.resolve_proposal(conn, ids[0], decision="approved")
    with get_engine().connect() as conn:
        after = repo.get_policy(conn)
        p = repo.get_proposal(conn, ids[0])
    assert p is not None
    assert p["status"] == "approved"
    assert before == after  # policy は触らない（提示専用）


# --- /chat：propose_trade のみで proposal だけ起票（journal は書かない）------


def _mock_complete(monkeypatch: pytest.MonkeyPatch, responses: list[LLMResponse]) -> None:
    async def _fake_complete(messages: Any, **_: Any) -> LLMResponse:
        return responses.pop(0)

    monkeypatch.setattr(service, "complete", _fake_complete)


def test_chat_propose_trade_only_creates_proposal_no_journal(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """propose_trade のみ（submit_journal なし）→ journal 0 件・journal_id None・proposal 1 件。"""
    repo.upsert_stocks([{"code": "72030", "company_name": "トヨタ自動車"}])
    _mock_complete(
        monkeypatch,
        [
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="t1",
                        name="propose_trade",
                        arguments={"action": "buy", "code": "72030", "reason": "好材料"},
                    )
                ],
            ),
            LLMResponse(content="買い提案を起票したのだ", tool_calls=[]),
        ],
    )

    res = client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "7203 の買いを提案して"}]},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["journal_id"] is None  # submit が無いので日記は残さない（ADR-029）

    with get_engine().connect() as conn:
        assert repo.list_journal(conn) == []
        props = repo.list_proposals(conn)
    assert len(props) == 1
    assert props[0]["kind"] == "buy"
    assert props[0]["journal_id"] is None


# --- nightly：trade 起票が journal と同一トランザクションで束ねられる ----------


def test_nightly_persists_trade_proposal(monkeypatch: pytest.MonkeyPatch, temp_db: None) -> None:
    """夜AI が propose_trade を呼ぶと journal と並んで buy/sell 提案が起票される。"""
    repo.upsert_stocks([{"code": "72030", "company_name": "トヨタ自動車"}])

    async def _fake_signals(_args: dict[str, object]) -> dict[str, object]:
        return {"date": "2026-06-11", "signals": []}

    async def _fake_metrics(_args: dict[str, object]) -> dict[str, object]:
        return {"portfolio_id": 1}

    async def _fake_overview(_args: dict[str, object]) -> dict[str, object]:
        return {"total_value": 1000.0}

    monkeypatch.setattr(handlers, "handle_get_signals", _fake_signals)
    monkeypatch.setattr(handlers, "handle_get_portfolio_metrics", _fake_metrics)
    monkeypatch.setattr(handlers, "handle_get_asset_overview", _fake_overview)

    async def _fake_loop(messages: Any, **_: Any) -> tuple[str, list[dict[str, object]]]:
        return "最終応答", [
            {"name": "submit_journal", "args": {"observations": "所見"}},
            {"name": "propose_trade", "args": {"action": "buy", "code": "72030", "reason": "材料"}},
        ]

    monkeypatch.setattr(nightly, "run_turn", _fake_loop)

    with get_engine().begin() as conn:
        _run(nightly.run_nightly_advisor(conn))

    with get_engine().connect() as conn:
        journals = repo.list_journal(conn)
        props = repo.list_proposals(conn)
    assert len(journals) == 1
    assert len(props) == 1
    assert props[0]["kind"] == "buy"
    assert props[0]["journal_id"] == journals[0]["id"]
