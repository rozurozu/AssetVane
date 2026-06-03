"""REST API テスト（/policy・/journal・/proposals・spec §8.2・§10）。

`client` フィクスチャ（alembic 経路で一時 SQLite を用意）で叩く。LLM/外部は使わない
（状態 API は LLM を呼ばない）。検証対象:
- GET/PUT /policy: core/rationale 分離・no_leverage int↔bool・sector_caps/exclusions の
  JSON↔型・部分更新。
- GET /journal・GET /proposals。
- approve/reject・approve の depends_on ガード（409）・不在 404。
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# /policy
# ---------------------------------------------------------------------------


def test_get_policy_returns_default_merged(client: Any) -> None:
    """policy 未設定でも DEFAULT がマージされ core/rationale 分離で返る。"""
    res = client.get("/policy")
    assert res.status_code == 200
    body = res.json()
    assert "core" in body and "rationale" in body
    core = body["core"]
    # DEFAULT_POLICY のマージ結果（no_leverage は int→bool）。
    assert core["no_leverage"] is True
    assert core["target_cash_ratio"] == 0.25
    assert core["sector_caps"] == {}
    assert core["exclusions"] == []


def test_put_policy_core_and_rationale(client: Any) -> None:
    """PUT /policy: core 更新（int↔bool・JSON↔型）＋ rationale 反映。"""
    res = client.put(
        "/policy",
        json={
            "core": {
                "risk_tolerance": "高",
                "max_position_weight": 0.3,
                "no_leverage": False,
                "sector_caps": {"3050": 0.4},
                "exclusions": ["7203", "6758"],
            },
            "rationale": "短期はリスクを取る",
        },
    )
    assert res.status_code == 200
    body = res.json()
    core = body["core"]
    assert core["risk_tolerance"] == "高"
    assert core["max_position_weight"] == 0.3
    assert core["no_leverage"] is False  # bool で返る
    assert core["sector_caps"] == {"3050": 0.4}  # JSON↔dict
    assert core["exclusions"] == ["7203", "6758"]  # JSON↔list
    assert body["rationale"] == "短期はリスクを取る"

    # 再取得で永続している。
    res2 = client.get("/policy")
    assert res2.json()["core"]["no_leverage"] is False


def test_put_policy_partial_update(client: Any) -> None:
    """部分更新: 指定列のみ変わり、他は DEFAULT/既存のまま。"""
    client.put("/policy", json={"core": {"risk_tolerance": "高"}})
    res = client.put("/policy", json={"core": {"time_horizon": "短"}})
    core = res.json()["core"]
    assert core["risk_tolerance"] == "高"  # 前回更新が残る
    assert core["time_horizon"] == "短"


def test_put_policy_core_change_writes_journal_snapshot(client: Any) -> None:
    """core 変更時は当日 journal に policy_snapshot が残る（理念のみ更新では残さない）。"""
    # core 変更 → journal が 1 件増える。
    client.put("/policy", json={"core": {"max_position_weight": 0.2}})
    entries = client.get("/journal").json()["entries"]
    assert len(entries) == 1
    assert entries[0]["policy_snapshot"]["max_position_weight"] == 0.2

    # rationale だけの更新では journal は増えない（即時更新・§6.5）。
    client.put("/policy", json={"rationale": "理念だけ更新"})
    entries2 = client.get("/journal").json()["entries"]
    assert len(entries2) == 1


# ---------------------------------------------------------------------------
# /journal
# ---------------------------------------------------------------------------


def test_get_journal_empty(client: Any) -> None:
    """journal 未生成なら空配列。"""
    res = client.get("/journal")
    assert res.status_code == 200
    assert res.json() == {"entries": []}


# ---------------------------------------------------------------------------
# /proposals + approve/reject
# ---------------------------------------------------------------------------


def _seed_proposal(client: Any, **fields: Any) -> int:
    """テスト用に proposal を直接 DB へ起票する（API には作成口が無いため）。"""
    import json

    from app.db import repo
    from app.db.engine import get_engine

    fields.setdefault("created_date", "2025-01-01")
    if isinstance(fields.get("body"), dict):
        fields["body"] = json.dumps(fields["body"])
    with get_engine().begin() as conn:
        return repo.insert_proposal(conn, **fields)


def test_get_proposals_and_filter(client: Any) -> None:
    """GET /proposals: status 絞り込み。"""
    _seed_proposal(client, kind="buy", body={"code": "7203"})
    _seed_proposal(client, kind="sell", body={"code": "6758"}, status="approved")

    all_props = client.get("/proposals").json()["proposals"]
    assert len(all_props) == 2
    pending = client.get("/proposals", params={"status": "pending"}).json()["proposals"]
    assert len(pending) == 1
    assert pending[0]["kind"] == "buy"
    assert pending[0]["body"] == {"code": "7203"}  # JSON↔dict


def test_approve_policy_change_updates_policy(client: Any) -> None:
    """POST approve: policy_change 承認で policy が更新される。"""
    pid = _seed_proposal(
        client,
        kind="policy_change",
        body={"field": "target_cash_ratio", "to": 0.4, "reason": "現金多め"},
    )
    res = client.post(f"/proposals/{pid}/approve", json={"outcome": "承認"})
    assert res.status_code == 200
    assert res.json()["proposal"]["status"] == "approved"

    core = client.get("/policy").json()["core"]
    assert core["target_cash_ratio"] == 0.4


def test_reject_proposal(client: Any) -> None:
    """POST reject: status=rejected に遷移。"""
    pid = _seed_proposal(client, kind="buy", body={"code": "7203"})
    res = client.post(f"/proposals/{pid}/reject", json={})
    assert res.status_code == 200
    assert res.json()["proposal"]["status"] == "rejected"


def test_approve_depends_on_guard_returns_409(client: Any) -> None:
    """depends_on が未承認なら approve は 409。"""
    parent = _seed_proposal(client, kind="policy_change", body={"field": "no_leverage", "to": 0})
    child = _seed_proposal(client, kind="buy", body={"code": "7203"}, depends_on=parent)

    res = client.post(f"/proposals/{child}/approve", json={})
    assert res.status_code == 409

    # 親を承認すれば子も承認できる。
    assert client.post(f"/proposals/{parent}/approve", json={}).status_code == 200
    assert client.post(f"/proposals/{child}/approve", json={}).status_code == 200


def test_approve_missing_returns_404(client: Any) -> None:
    """存在しない proposal の approve は 404。"""
    res = client.post("/proposals/9999/approve", json={})
    assert res.status_code == 404
