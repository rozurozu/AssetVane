"""軸2 昼チャットの journal 昇格テスト（POST /chat・phase3-spec.md §5・§6.3・ADR-018/029/013）。

`client` フィクスチャで叩き、LLM（service.complete）は必ずモック（ネットを叩かない）。検証対象:
- チャットで submit_journal を呼ぶと advisor_journal が 1 件（source='chat'・situation_briefing
  は None）残り、レスポンスに journal_id（int）が載ること。
- observations 空・最終 reply 空の縮退ターンは journal を書かず journal_id is None（ADR-018）。
- proposed_policy_change が単一 {field,to} なら proposals が 1 件（policy_change・pending・
  journal_id 一致）起票されること（ADR-013）。
- submit_journal を呼ばない通常応答は journal 0 件・journal_id is None（自動保存しない・ADR-029）。
- 多列 patch は journal は残るが適用不能 proposal を起票しない（coerce 昼回帰・U-10 裁定①）。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.advisor import service
from app.advisor.llm import LLMResponse, ToolCall
from app.db import repo
from app.db.engine import get_engine


def _submit_then_reply(submit_args: dict[str, Any], final_reply: str) -> list[LLMResponse]:
    """submit_journal を 1 回呼んでから最終 reply を返す 2 往復分の LLM 応答列を組む。"""
    return [
        LLMResponse(
            content=None,
            tool_calls=[ToolCall(id="j1", name="submit_journal", arguments=submit_args)],
        ),
        LLMResponse(content=final_reply, tool_calls=[]),
    ]


def _mock_complete(monkeypatch: pytest.MonkeyPatch, responses: list[LLMResponse]) -> None:
    """service.complete を応答列の順送りに差し替える（ネットに出ない）。"""

    async def _fake_complete(messages: Any, **_: Any) -> LLMResponse:
        return responses.pop(0)

    monkeypatch.setattr(service, "complete", _fake_complete)


def test_chat_submit_journal_records_journal(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """submit_journal で journal 1 件（source='chat'・briefing None）残り journal_id が int。"""
    _mock_complete(
        monkeypatch,
        _submit_then_reply(
            {"observations": "昼の所見", "proposal": "現金を厚めに"},
            "日記に残しました",
        ),
    )

    res = client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "この会話を日記に残して"}]},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["reply"] == "日記に残しました"
    assert isinstance(body["journal_id"], int)

    with get_engine().connect() as conn:
        journals = repo.list_journal(conn)
        detail = repo.get_journal(conn, body["journal_id"])
    assert len(journals) == 1
    j = journals[0]
    assert j["source"] == "chat"
    assert j["observations"] == "昼の所見"
    assert j["proposal"] == "現金を厚めに"
    # 軸2 は監査用 briefing を持たない（画面コンテキストのみ＝ADR-025/029）。
    assert detail is not None
    assert detail["situation_briefing"] is None


def test_chat_empty_observations_skips_journal(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """observations 空・最終 reply 空の縮退ターンは journal を書かず journal_id None（ADR-018）。"""
    _mock_complete(
        monkeypatch,
        _submit_then_reply({"observations": ""}, ""),
    )

    res = client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "日記に残して"}]},
    )
    assert res.status_code == 200
    assert res.json()["journal_id"] is None

    with get_engine().connect() as conn:
        assert repo.list_journal(conn) == []


def test_chat_submit_journal_with_policy_change_creates_proposal(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """単一 {field,to} の方針変更案で proposal（policy_change・pending・journal_id 一致）を起票。"""
    _mock_complete(
        monkeypatch,
        _submit_then_reply(
            {
                "observations": "現金比率を見直したい",
                "proposed_policy_change": {"field": "target_cash_ratio", "to": 0.4},
            },
            "方針変更案を起票しました",
        ),
    )

    res = client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "現金比率を 40% に上げて日記に残して"}]},
    )
    assert res.status_code == 200
    journal_id = res.json()["journal_id"]
    assert isinstance(journal_id, int)

    with get_engine().connect() as conn:
        proposals = repo.list_proposals(conn)
    assert len(proposals) == 1
    p = proposals[0]
    assert p["kind"] == "policy_change"
    assert p["status"] == "pending"
    assert p["journal_id"] == journal_id
    import json

    assert json.loads(p["body"])["field"] == "target_cash_ratio"
    assert json.loads(p["body"])["to"] == 0.4


def test_chat_no_submit_records_nothing(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """submit_journal なしの通常応答は journal 0 件・journal_id None（自動保存しない・ADR-029）。"""

    async def _fake_complete(messages: Any, **_: Any) -> LLMResponse:
        return LLMResponse(content="ふつうの相談応答", tool_calls=[])

    monkeypatch.setattr(service, "complete", _fake_complete)

    res = client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "最近どう?"}]},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["reply"] == "ふつうの相談応答"
    assert body["journal_id"] is None

    with get_engine().connect() as conn:
        assert repo.list_journal(conn) == []


def test_chat_empty_observations_falls_back_to_reply(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """observations 空でも最終 reply 非空なら reply を所見に記録する（昼の fallback・ADR-018）。

    昼/夜で共通サービスの reply フォールバックが対称に効くことを明示する。明示的に submit を
    呼んだターン（has_submit=True）なので ADR-029 の自動保存禁止には抵触しない。
    """
    _mock_complete(
        monkeypatch,
        _submit_then_reply({"observations": ""}, "最終的な所見だけは述べた"),
    )

    res = client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "日記に残して"}]},
    )
    assert res.status_code == 200
    journal_id = res.json()["journal_id"]
    assert isinstance(journal_id, int)

    with get_engine().connect() as conn:
        journals = repo.list_journal(conn)
    assert len(journals) == 1
    assert journals[0]["source"] == "chat"
    assert journals[0]["observations"] == "最終的な所見だけは述べた"


def test_chat_multi_field_patch_keeps_journal_no_proposal(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """多列 patch は journal を残すが適用不能 proposal は起票しない（coerce 昼回帰・U-10①）。"""
    _mock_complete(
        monkeypatch,
        _submit_then_reply(
            {
                "observations": "複数列を直したい",
                "proposed_policy_change": {
                    "max_position_weight": 0.2,
                    "target_cash_ratio": 0.4,
                },
            },
            "記録しました",
        ),
    )

    res = client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "日記に残して"}]},
    )
    assert res.status_code == 200
    journal_id = res.json()["journal_id"]
    assert isinstance(journal_id, int)

    with get_engine().connect() as conn:
        journals = repo.list_journal(conn)
        proposals = repo.list_proposals(conn)
    assert len(journals) == 1
    assert journals[0]["proposed_policy_change"] is None
    assert proposals == []
