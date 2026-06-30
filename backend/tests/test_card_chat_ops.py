"""チャットからのカード整備 tool（propose_card / adjust_card_weight）を検証（ADR-062 追補）。

担保: persist_card_ops_from_tool_runs が propose_card→draft カード起票（title 空は本文先頭で代替）・
adjust_card_weight→proposals(kind=card_weight) 起票（承認制・存在しないカードは skip）。
resolve_proposal(approved) が card_weight を反映・rejected では変えない。handler は read-only 検証で
{ok}/{error} を返す。/chat 応答が起票 draft の id を card_ids で可視化する（ADR-065）。
一時 SQLite・LLM/ネット非依存。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.advisor import service
from app.advisor.journaling import persist_card_ops_from_tool_runs
from app.advisor.llm import LLMResponse, ToolCall
from app.advisor.tools import handlers
from app.db import repo
from app.db.engine import get_engine


def _run(name: str, args: dict[str, object]) -> dict[str, object]:
    return {"name": name, "args": args}


@pytest.mark.usefixtures("temp_db")
def test_persist_propose_card_creates_draft() -> None:
    """propose_card は draft カードを起票する（人間が active 化＝ADR-009）。"""
    runs = [_run("propose_card", {"body": "本文知識", "title": "T", "level": "market"})]
    with get_engine().begin() as conn:
        result = persist_card_ops_from_tool_runs(conn, tool_runs=runs, date="2026-06-26")
    assert len(result["cards"]) == 1
    with get_engine().connect() as conn:
        card = repo.get_knowledge_card(conn, result["cards"][0])
    assert card is not None
    assert card["status"] == "draft"
    assert card["title"] == "T"
    assert card["level"] == "market"


@pytest.mark.usefixtures("temp_db")
def test_persist_propose_card_title_fallback() -> None:
    """title なしでも本文先頭で代替して起票する。"""
    runs = [_run("propose_card", {"body": "先頭行\n2 行目"})]
    with get_engine().begin() as conn:
        result = persist_card_ops_from_tool_runs(conn, tool_runs=runs, date="2026-06-26")
    with get_engine().connect() as conn:
        card = repo.get_knowledge_card(conn, result["cards"][0])
    assert card is not None
    assert card["title"]  # 本文先頭で代替


@pytest.mark.usefixtures("temp_db")
def test_persist_adjust_weight_creates_proposal() -> None:
    """adjust_card_weight は proposals(kind=card_weight・pending) を起票する。"""
    cid = repo.insert_knowledge_card(title="c", body="b", status="active")
    runs = [_run("adjust_card_weight", {"card_id": cid, "weight": 0.5, "reason": "古い"})]
    with get_engine().begin() as conn:
        result = persist_card_ops_from_tool_runs(conn, tool_runs=runs, date="2026-06-26")
    assert len(result["weight_proposals"]) == 1
    with get_engine().connect() as conn:
        prop = repo.get_proposal(conn, result["weight_proposals"][0])
    assert prop is not None
    assert prop["kind"] == "card_weight"
    assert prop["status"] == "pending"
    body = json.loads(prop["body"])
    assert body["card_id"] == cid
    assert body["weight"] == 0.5


@pytest.mark.usefixtures("temp_db")
def test_persist_adjust_weight_skips_missing_card() -> None:
    """存在しないカードへの weight 変更は起票しない（幻覚 id を queue に入れない・ADR-018）。"""
    runs = [_run("adjust_card_weight", {"card_id": 9999, "weight": 0.5, "reason": "x"})]
    with get_engine().begin() as conn:
        result = persist_card_ops_from_tool_runs(conn, tool_runs=runs, date="2026-06-26")
    assert result["weight_proposals"] == []


@pytest.mark.usefixtures("temp_db")
def test_resolve_card_weight_applies_on_approve() -> None:
    """card_weight proposal を承認すると weight が反映される。"""
    cid = repo.insert_knowledge_card(title="c", body="b", status="active")
    with get_engine().begin() as conn:
        pid = repo.insert_proposal(
            conn,
            created_date="2026-06-26",
            kind="card_weight",
            body=json.dumps({"card_id": cid, "weight": 0.3}),
            rationale="古い",
            status="pending",
        )
    with get_engine().begin() as conn:
        service.resolve_proposal(conn, pid, decision="approved")
    with get_engine().connect() as conn:
        card = repo.get_knowledge_card(conn, cid)
    assert card is not None
    assert card["weight"] == 0.3


@pytest.mark.usefixtures("temp_db")
def test_resolve_card_weight_not_applied_on_reject() -> None:
    """却下では weight は変わらない（既定 1.0 のまま）。"""
    cid = repo.insert_knowledge_card(title="c", body="b", status="active")
    with get_engine().begin() as conn:
        pid = repo.insert_proposal(
            conn,
            created_date="2026-06-26",
            kind="card_weight",
            body=json.dumps({"card_id": cid, "weight": 0.3}),
            rationale="r",
            status="pending",
        )
    with get_engine().begin() as conn:
        service.resolve_proposal(conn, pid, decision="rejected")
    with get_engine().connect() as conn:
        card = repo.get_knowledge_card(conn, cid)
    assert card is not None
    assert card["weight"] == 1.0


def test_handle_propose_card_validates() -> None:
    """handle_propose_card は read-only 検証（空 body は error）。"""
    assert asyncio.run(handlers.handle_propose_card({"body": "知識"}))["ok"] is True
    assert "error" in asyncio.run(handlers.handle_propose_card({"body": "  "}))


@pytest.mark.usefixtures("temp_db")
def test_handle_adjust_card_weight_validates() -> None:
    """handle_adjust_card_weight は存在確認＋weight>0 を検証する。"""
    cid = repo.insert_knowledge_card(title="c", body="b", status="active")
    ok = asyncio.run(
        handlers.handle_adjust_card_weight({"card_id": cid, "weight": 2.0, "reason": "r"})
    )
    assert ok["ok"] is True
    missing = asyncio.run(
        handlers.handle_adjust_card_weight({"card_id": 9999, "weight": 2.0, "reason": "r"})
    )
    assert "error" in missing
    bad = asyncio.run(
        handlers.handle_adjust_card_weight({"card_id": cid, "weight": -1.0, "reason": "r"})
    )
    assert "error" in bad


# --- /chat：propose_card が起票した draft を card_ids で可視化する（ADR-065）---


def _mock_complete(monkeypatch: pytest.MonkeyPatch, responses: list[LLMResponse]) -> None:
    async def _fake_complete(messages: Any, **_: Any) -> LLMResponse:
        return responses.pop(0)

    monkeypatch.setattr(service, "complete", _fake_complete)


def test_chat_propose_card_surfaces_card_ids(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """propose_card を呼ぶと /chat 応答 card_ids に起票 draft の id が載る（ADR-065）。

    壁打ち→合意→起票のフィードバックを frontend がインライン表示する契約（journal_id と同型）。
    """
    _mock_complete(
        monkeypatch,
        [
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="propose_card",
                        arguments={"body": "日銀の政策修正は内需株に効きやすい", "level": "market"},
                    )
                ],
            ),
            LLMResponse(content="知識ノートの下書きを起票したのだ", tool_calls=[]),
        ],
    )

    res = client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "この見方をノートにして"}]},
    )
    assert res.status_code == 200
    body = res.json()
    assert len(body["card_ids"]) == 1
    # 起票された id は実在の draft カードを指す（人間が /cards で active 化＝ADR-009）。
    with get_engine().connect() as conn:
        card = repo.get_knowledge_card(conn, body["card_ids"][0])
    assert card is not None
    assert card["status"] == "draft"


def test_chat_no_card_op_returns_empty_card_ids(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """カード操作の無い通常ターンでは card_ids は空（書き込み接続も開かない）。"""
    _mock_complete(monkeypatch, [LLMResponse(content="ふつうの応答なのだ", tool_calls=[])])
    res = client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "こんにちは"}]},
    )
    assert res.status_code == 200
    assert res.json()["card_ids"] == []
