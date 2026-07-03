"""投資家プロファイル API（/profile・/profile/notes）を担保する（ADR-082・ループ ④）。

client（alembic lifespan・本番と同じ経路）で GET/PUT /profile の往復・pending 傾向メモ一覧・
既存 /proposals 承認での本文追記を検証する（testing-strategy・承認制＝人間承認で育つ＝ADR-009）。
"""

from __future__ import annotations

import json
from typing import Any

from app.db import repo
from app.db.engine import get_engine
from app.db.schema import proposals


def test_get_profile_empty_initially(client: Any) -> None:
    """未育成の投資家プロファイルは空文字で返る（初回は行が無い）。"""
    res = client.get("/profile")
    assert res.status_code == 200
    assert res.json() == {"body": "", "updated_at": None}


def test_put_then_get_profile_roundtrip(client: Any) -> None:
    """PUT で手編集した本文が GET に反映される（人間による全文編集）。"""
    res = client.put("/profile", json={"body": "急落で狼狽売りしがち。"})
    assert res.status_code == 200
    assert res.json()["body"] == "急落で狼狽売りしがち。"

    got = client.get("/profile")
    assert got.json()["body"] == "急落で狼狽売りしがち。"
    assert got.json()["updated_at"] is not None


def test_profile_notes_lists_pending_and_approve_appends(client: Any) -> None:
    """pending 傾向メモが /profile/notes に出て、/proposals 承認で本文へ追記される。"""
    with get_engine().begin() as conn:
        pid = repo.insert_proposal(
            conn,
            created_date="2026-07-03",
            kind="profile_note",
            body=json.dumps({"text": "損切りが遅い", "evidence": "負けの平均保有 90日"}),
            status="pending",
        )
        # profile_note でない pending は /profile/notes に混ざらない。
        repo.insert_proposal(
            conn, created_date="2026-07-03", kind="buy", body="{}", status="pending"
        )

    notes = client.get("/profile/notes").json()
    assert len(notes) == 1
    assert notes[0]["id"] == pid
    assert notes[0]["text"] == "損切りが遅い"
    assert notes[0]["evidence"] == "負けの平均保有 90日"

    # 既存の承認エンドポイント（kind 非依存）で承認 → 本文へ追記。
    res = client.post(f"/proposals/{pid}/approve")
    assert res.status_code == 200
    assert "損切りが遅い" in client.get("/profile").json()["body"]

    # 承認後は pending から消える。
    assert client.get("/profile/notes").json() == []


def test_profile_note_reject_does_not_append(client: Any) -> None:
    """却下した傾向メモは本文へ追記されない（人間が拒否したら反映しない）。"""
    with get_engine().begin() as conn:
        pid = repo.insert_proposal(
            conn,
            created_date="2026-07-03",
            kind="profile_note",
            body=json.dumps({"text": "却下される癖", "evidence": "e"}),
            status="pending",
        )
    client.post(f"/proposals/{pid}/reject")
    assert client.get("/profile").json()["body"] == ""
    # proposals 側は rejected に遷移している。
    with get_engine().connect() as conn:
        row = conn.execute(proposals.select().where(proposals.c.id == pid)).mappings().first()
    assert row is not None
    assert row["status"] == "rejected"
