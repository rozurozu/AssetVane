"""投資家プロファイルの傾向メモ（profile_note）の Tool→永続→承認反映を担保する（ADR-082）。

propose_profile_note は allowlist_only で profiler 面にだけ露出し chat/nightly には隠れること・
persist_profile_notes_from_tool_runs が pending 起票し同一 text を dedup すること・承認で
apply_profile_note が投資家プロファイル本文へ日付付きで追記すること・壊れた body は skip する
ことを一時 SQLite で検証する（testing-strategy・承認制＝人間承認でのみ本文が育つ＝ADR-009）。
"""

from __future__ import annotations

import json

from app.advisor import journaling, service
from app.advisor.tools import registry
from app.db import repo
from app.db.engine import get_engine


def test_profile_note_hidden_from_chat_visible_to_profiler():
    """allowlist_only の propose_profile_note は chat/nightly に出ず profiler にだけ出る。"""
    default = {t["function"]["name"] for t in registry.openai_tools(7)}  # type: ignore[index]
    assert "propose_profile_note" not in default
    prof = {
        t["function"]["name"]  # type: ignore[index]
        for t in registry.openai_tools(7, allow=registry.PROFILER_TOOLSET)
    }
    assert "propose_profile_note" in prof


def test_persist_inserts_pending_profile_note(temp_db):
    """propose_profile_note が proposals に pending（kind='profile_note'）で起票される。"""
    tool_runs = [
        {
            "name": "propose_profile_note",
            "args": {"text": "急落で狼狽売りしがち", "evidence": "売り後上昇率 70%"},
        }
    ]
    with get_engine().begin() as conn:
        ids = journaling.persist_profile_notes_from_tool_runs(
            conn, tool_runs=tool_runs, date="2026-07-03"
        )
    assert len(ids) == 1
    with get_engine().connect() as conn:
        rows = repo.list_proposals(conn, status="pending")
    assert len(rows) == 1
    assert rows[0]["kind"] == "profile_note"
    assert json.loads(rows[0]["body"])["text"] == "急落で狼狽売りしがち"


def test_persist_dedup_same_text(temp_db):
    """同一 text の profile_note は 1 件だけ起票する（pending dedup・毎晩の氾濫を抑える）。"""
    tool_runs = [
        {"name": "propose_profile_note", "args": {"text": "同じ癖", "evidence": "e1"}},
        {"name": "propose_profile_note", "args": {"text": "同じ癖", "evidence": "e2"}},
    ]
    with get_engine().begin() as conn:
        ids = journaling.persist_profile_notes_from_tool_runs(
            conn, tool_runs=tool_runs, date="2026-07-03"
        )
    assert len(ids) == 1  # 2 件目は同一 begin 内でも pending として見えて dedup


def test_persist_skips_empty_text(temp_db):
    """空 text の profile_note は起票せずスキップ（検証で弾く）。"""
    tool_runs = [{"name": "propose_profile_note", "args": {"text": "   ", "evidence": "e"}}]
    with get_engine().begin() as conn:
        ids = journaling.persist_profile_notes_from_tool_runs(
            conn, tool_runs=tool_runs, date="2026-07-03"
        )
    assert ids == []


def test_approve_profile_note_appends_body(temp_db):
    """profile_note の承認で投資家プロファイル本文へ日付付きで追記される（増分・人間承認）。"""
    body = json.dumps(
        {"text": "損切りが遅い", "evidence": "負けの平均保有 90日"}, ensure_ascii=False
    )
    with get_engine().begin() as conn:
        pid = repo.insert_proposal(
            conn, created_date="2026-07-03", kind="profile_note", body=body, status="pending"
        )
    with get_engine().begin() as conn:
        service.resolve_proposal(conn, pid, decision="approved")

    with get_engine().connect() as conn:
        prof = repo.get_investor_profile(conn)
        rows = repo.list_proposals(conn, status="approved")
    assert "損切りが遅い" in prof["body"]
    assert len(rows) == 1  # status も approved に遷移


def test_approve_appends_and_preserves_prior(temp_db):
    """既に本文があるとき、承認は上書きせず末尾に追記する（active は育てる）。"""
    with get_engine().begin() as conn:
        repo.upsert_investor_profile(conn, "既存のプロファイル本文。")
        pid = repo.insert_proposal(
            conn,
            created_date="2026-07-03",
            kind="profile_note",
            body=json.dumps({"text": "新しい癖", "evidence": "e"}, ensure_ascii=False),
            status="pending",
        )
    with get_engine().begin() as conn:
        service.resolve_proposal(conn, pid, decision="approved")
    with get_engine().connect() as conn:
        text = repo.get_investor_profile(conn)["body"]
    assert "既存のプロファイル本文。" in text
    assert "新しい癖" in text


def test_apply_profile_note_broken_body_skips(temp_db):
    """壊れた body（非 JSON）は追記せず skip（落とさない・ADR-018）。"""
    with get_engine().begin() as conn:
        service.apply_profile_note(conn, "not-json")
    with get_engine().connect() as conn:
        assert repo.get_investor_profile(conn)["body"] == ""
