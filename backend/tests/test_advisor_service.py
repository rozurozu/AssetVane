"""dispatch ループ・状態遷移のテスト（phase3-spec.md §4.2・§8.1・§10）。

LLM（complete）は必ずモック、DB は temp_db（一時 SQLite）。検証対象:
- run_tool_loop: tool_calls→handler→tool ロール挿入→再 complete の往復・max_rounds 打ち切り・
  未知 tool で落ちない・tool_runs に結果値が載らない。
- resolve_proposal: pending→approved（policy_change なら policy 更新＋journal snapshot）／
  →rejected／depends_on 未承認で approve 弾く／buy 承認が約定を起こさない（status だけ動く）。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.advisor import service
from app.advisor.llm import LLMResponse, ToolCall
from app.advisor.tools.registry import ToolDef
from app.db import repo
from app.db.engine import get_engine


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _stub_handler(monkeypatch: pytest.MonkeyPatch, name: str, handler: Any) -> None:
    """REGISTRY[name] を handler 差し替え版の ToolDef に入れ替える（ToolDef は frozen のため）。"""
    base = service.REGISTRY[name]
    replaced = ToolDef(
        name=base.name,
        description=base.description,
        parameters=base.parameters,
        handler=handler,
        min_phase=base.min_phase,
    )
    monkeypatch.setitem(service.REGISTRY, name, replaced)


# ---------------------------------------------------------------------------
# run_tool_loop
# ---------------------------------------------------------------------------


def test_run_tool_loop_resolves_tool_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """1 往復: tool_call → handler 実行 → tool ロール挿入 → 再 complete で最終テキスト。"""
    # 1 回目は tool_call、2 回目は最終テキストを返す complete モック。
    responses = [
        LLMResponse(
            content=None,
            tool_calls=[ToolCall(id="c1", name="get_signals", arguments={"type": "momentum"})],
        ),
        LLMResponse(content="最終応答", tool_calls=[]),
    ]
    seen_messages: list[Any] = []

    async def _fake_complete(messages: Any, **_: Any) -> LLMResponse:
        seen_messages.append([dict(m) for m in messages])
        return responses.pop(0)

    async def _fake_handler(args: dict[str, object]) -> dict[str, object]:
        return {"date": "2025-01-01", "signals": [{"code": "7203", "score": 0.9}]}

    monkeypatch.setattr(service, "complete", _fake_complete)
    _stub_handler(monkeypatch, "get_signals", _fake_handler)

    reply, tool_runs = _run(service.run_tool_loop([{"role": "user", "content": "兆候は?"}]))

    assert reply == "最終応答"
    # tool_runs には呼んだ Tool 名と引数のみ（結果の数値は載らない＝ADR-025）。
    assert tool_runs == [{"name": "get_signals", "args": {"type": "momentum"}}]
    flat = json.dumps(tool_runs, ensure_ascii=False)
    assert "0.9" not in flat and "7203" not in flat
    # 2 回目の complete には tool ロールが挿入されている。
    second = seen_messages[1]
    assert any(m["role"] == "tool" and m["tool_call_id"] == "c1" for m in second)
    assert any(m["role"] == "assistant" and "tool_calls" in m for m in second)


def test_run_tool_loop_unknown_tool_does_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    """未知 Tool 名でもループは落ちず、{"error": "unknown tool"} を tool ロールに入れて続行。"""
    responses = [
        LLMResponse(
            content=None,
            tool_calls=[ToolCall(id="c1", name="does_not_exist", arguments={})],
        ),
        LLMResponse(content="リカバリ応答", tool_calls=[]),
    ]
    captured: list[Any] = []

    async def _fake_complete(messages: Any, **_: Any) -> LLMResponse:
        captured.append([dict(m) for m in messages])
        return responses.pop(0)

    monkeypatch.setattr(service, "complete", _fake_complete)

    reply, tool_runs = _run(service.run_tool_loop([{"role": "user", "content": "x"}]))

    assert reply == "リカバリ応答"
    assert tool_runs == [{"name": "does_not_exist", "args": {}}]
    tool_msg = next(m for m in captured[1] if m["role"] == "tool")
    assert json.loads(tool_msg["content"]) == {"error": "unknown tool"}


def test_run_tool_loop_max_rounds_cutoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """tool_call が止まらない場合は max_rounds で打ち切る（無限ループ防止）。"""

    async def _always_tool(messages: Any, **_: Any) -> LLMResponse:
        return LLMResponse(
            content="まだ続けたい",
            tool_calls=[ToolCall(id="c", name="get_signals", arguments={})],
        )

    async def _fake_handler(args: dict[str, object]) -> dict[str, object]:
        return {"ok": True}

    monkeypatch.setattr(service, "complete", _always_tool)
    _stub_handler(monkeypatch, "get_signals", _fake_handler)

    reply, tool_runs = _run(service.run_tool_loop([{"role": "user", "content": "x"}], max_rounds=2))

    # 2 往復で打ち切り、最後の content を返す。
    assert reply == "まだ続けたい"
    assert len(tool_runs) == 2


# ---------------------------------------------------------------------------
# resolve_proposal
# ---------------------------------------------------------------------------


def _insert_proposal(**fields: Any) -> int:
    fields.setdefault("created_date", "2025-01-01")
    with get_engine().begin() as conn:
        return repo.insert_proposal(conn, **fields)


def test_resolve_proposal_policy_change_updates_policy(temp_db: None) -> None:
    """policy_change 承認で policy が更新され、当日 journal に snapshot が残る。"""
    body = json.dumps({"field": "max_position_weight", "to": 0.2, "reason": "集中したい"})
    pid = _insert_proposal(kind="policy_change", body=body, rationale="r")

    with get_engine().begin() as conn:
        service.resolve_proposal(conn, pid, decision="approved")

    with get_engine().connect() as conn:
        policy = repo.get_policy(conn)
        assert policy is not None
        assert policy["max_position_weight"] == pytest.approx(0.2)
        prop = repo.get_proposal(conn, pid)
        assert prop is not None and prop["status"] == "approved"
        journals = repo.list_journal(conn)
    # policy_change 承認は journal_id が無い起票なので当日 journal に snapshot が残る。
    assert journals, "policy 変更の snapshot 用 journal が残るはず"
    snapshot = json.loads(journals[0]["policy_snapshot"])
    assert snapshot["max_position_weight"] == pytest.approx(0.2)


def test_resolve_proposal_sector_caps_dict_roundtrip(temp_db: None) -> None:
    """sector_caps の dict `to` を承認しても落ちず、DB は JSON 文字列・読み出しは dict（ADR-013）。

    encode_policy_field を通さず dict のまま TEXT 列にバインドすると sqlite3 エラーで
    承認がクラッシュしていた回帰。snapshot も単エンコード（JSON 文字列の入れ子なし）を確認。
    """
    from app.services import policy as policy_service

    body = json.dumps({"field": "sector_caps", "to": {"3050": 0.4}, "reason": "集中回避"})
    pid = _insert_proposal(kind="policy_change", body=body)

    with get_engine().begin() as conn:
        service.resolve_proposal(conn, pid, decision="approved")

    with get_engine().connect() as conn:
        raw = repo.get_policy(conn)
        assert raw is not None
        # 生行は JSON 文字列（DB 形）。
        assert json.loads(raw["sector_caps"]) == {"3050": 0.4}
        # services 経由は dict（正規化済み）。
        assert policy_service.get_policy(conn)["sector_caps"] == {"3050": 0.4}
        journals = repo.list_journal(conn)
    # snapshot は単エンコード（sector_caps が入れ子の JSON 文字列でなく dict で読める）。
    snapshot = json.loads(journals[0]["policy_snapshot"])
    assert snapshot["sector_caps"] == {"3050": 0.4}


def test_resolve_proposal_no_leverage_bool_to_int(temp_db: None) -> None:
    """no_leverage の bool `to` は 0/1 で保存される（PUT /policy と同じ変換・ADR-013）。"""
    pid = _insert_proposal(
        kind="policy_change",
        body=json.dumps({"field": "no_leverage", "to": True}),
    )
    with get_engine().begin() as conn:
        service.resolve_proposal(conn, pid, decision="approved")

    with get_engine().connect() as conn:
        raw = repo.get_policy(conn)
        assert raw is not None
        assert raw["no_leverage"] == 1


def test_resolve_proposal_reject(temp_db: None) -> None:
    """却下は status=rejected に遷移し policy は変えない。"""
    pid = _insert_proposal(
        kind="policy_change",
        body=json.dumps({"field": "target_cash_ratio", "to": 0.5}),
    )
    with get_engine().begin() as conn:
        service.resolve_proposal(conn, pid, decision="rejected", outcome="様子見")

    with get_engine().connect() as conn:
        prop = repo.get_proposal(conn, pid)
        assert prop is not None
        assert prop["status"] == "rejected"
        assert prop["outcome"] == "様子見"
        # policy 行は作られていない（変更しないので upsert も起きない）。
        assert repo.get_policy(conn) is None


def test_resolve_proposal_depends_on_guard(temp_db: None) -> None:
    """depends_on が未承認の間は approve を弾く（ValueError・承認順制御）。"""
    parent = _insert_proposal(
        kind="policy_change",
        body=json.dumps({"field": "no_leverage", "to": 1}),
    )
    child = _insert_proposal(
        kind="buy",
        body=json.dumps({"code": "7203", "shares": 100}),
        depends_on=parent,
    )

    # 親が pending の間は子を承認できない。
    with pytest.raises(ValueError), get_engine().begin() as conn:
        service.resolve_proposal(conn, child, decision="approved")

    # 親を承認 → 子も承認できるようになる。
    with get_engine().begin() as conn:
        service.resolve_proposal(conn, parent, decision="approved")
    with get_engine().begin() as conn:
        service.resolve_proposal(conn, child, decision="approved")
    with get_engine().connect() as conn:
        child_prop = repo.get_proposal(conn, child)
        assert child_prop is not None
        assert child_prop["status"] == "approved"


def test_resolve_proposal_buy_does_not_execute(temp_db: None) -> None:
    """buy 承認は約定を起こさない（status だけ動く・ADR-001/019）。"""
    pid = _insert_proposal(kind="buy", body=json.dumps({"code": "7203", "shares": 100}))
    with get_engine().begin() as conn:
        service.resolve_proposal(conn, pid, decision="approved")

    with get_engine().connect() as conn:
        prop = repo.get_proposal(conn, pid)
        assert prop is not None
        assert prop["status"] == "approved"
        # transactions / holdings は触られない（約定なし）。
        assert repo.list_transactions(conn, 1) == []
        # policy も変わらない（buy は policy_change ではない）。
        assert repo.get_policy(conn) is None


def test_resolve_proposal_missing_raises_keyerror(temp_db: None) -> None:
    """存在しない proposal は KeyError（ルータが 404 に翻訳）。"""
    with pytest.raises(KeyError), get_engine().begin() as conn:
        service.resolve_proposal(conn, 999, decision="approved")
