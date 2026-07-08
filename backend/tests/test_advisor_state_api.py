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


def test_put_policy_snapshot_single_encoded(client: Any) -> None:
    """journal の policy_snapshot は単エンコード＝sector_caps が dict で読める（ADR-013）。

    repo の生行（JSON 文字列入り）をそのまま json.dumps すると snapshot の中に
    JSON 文字列が入れ子で埋まる（二重エンコード）。dumps 前の normalize_policy_row で
    型へ直してから焼く回帰テスト。
    """
    client.put("/policy", json={"core": {"sector_caps": {"3050": 0.4}}})
    entries = client.get("/journal").json()["entries"]
    snapshot = entries[0]["policy_snapshot"]
    # 入れ子の JSON 文字列でなく dict のまま読める。
    assert snapshot["sector_caps"] == {"3050": 0.4}


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


# ---------------------------------------------------------------------------
# /advisor/turns（判断軌跡の観測層・ADR-092）
# ---------------------------------------------------------------------------


def _seed_turn(client: Any, **fields: Any) -> int:
    """テスト用に advisor_turns を直接 DB へ焼く（LLM 不要・観測層の検証用・ADR-092）。"""
    import json

    from app.db import repo
    from app.db.engine import get_engine

    if isinstance(fields.get("tool_sequence"), list):
        fields["tool_sequence"] = json.dumps(fields["tool_sequence"], ensure_ascii=False)
    with get_engine().begin() as conn:
        return repo.insert_turn(conn, **fields)


def test_get_advisor_turns_summary_and_recent(client: Any) -> None:
    """GET /advisor/turns: 面別サマリ（集計）＋直近の軌跡（導出フラグ込み）を返す。"""
    # nightly: 規律を満たした起票ターン（disciplined=1）。
    _seed_turn(
        client,
        source="nightly",
        model="m",
        tool_sequence=[
            {"name": "get_signals", "args": {"type": "momentum"}},
            {"name": "propose_trade", "args": {"code": "7203"}},
            {"name": "submit_journal", "args": {"observations": "x"}},
        ],
        n_rounds=2,
        truncated=0,
        called_propose_trade=1,
        propose_trade_disciplined=1,
    )
    # nightly: propose_trade 非該当（disciplined=NULL）。
    _seed_turn(
        client,
        source="nightly",
        model="m",
        tool_sequence=[{"name": "submit_notable_stocks", "args": {"picks": []}}],
        n_rounds=1,
        truncated=1,
        called_propose_trade=0,
        propose_trade_disciplined=None,
    )
    # chat: 別面（サマリが source 別に割れることの確認）。
    _seed_turn(
        client,
        source="chat",
        model="m",
        tool_sequence=[{"name": "get_signals", "args": {}}],
        n_rounds=1,
        truncated=0,
        called_propose_trade=0,
        propose_trade_disciplined=None,
    )

    body = client.get("/advisor/turns").json()

    # サマリは source 別（chat, nightly の 2 行）。
    by_source = {row["source"]: row for row in body["summary"]}
    assert set(by_source) == {"chat", "nightly"}
    nightly = by_source["nightly"]
    assert nightly["n_turns"] == 2
    assert nightly["avg_rounds"] == 1.5
    assert nightly["truncated_rate"] == 0.5
    assert nightly["n_propose_trade"] == 1
    # disciplined_rate は起票ターン（NOT NULL）だけの平均＝1.0（NULL 無視・ADR-084 同型）。
    assert nightly["disciplined_rate"] == 1.0

    # recent は created_at 降順。導出フラグ（submit_journal/notable）が tool_sequence から立つ。
    recent = body["recent"]
    assert len(recent) == 3
    disciplined_turn = next(t for t in recent if t["called_propose_trade"])
    assert disciplined_turn["called_submit_journal"] is True
    assert disciplined_turn["propose_trade_disciplined"] is True
    assert [c["name"] for c in disciplined_turn["tool_sequence"]] == [
        "get_signals",
        "propose_trade",
        "submit_journal",
    ]
    notable_turn = next(t for t in recent if t["truncated"])
    assert notable_turn["called_submit_notable"] is True
    assert notable_turn["propose_trade_disciplined"] is None


def test_get_advisor_turns_source_filter(client: Any) -> None:
    """GET /advisor/turns?source=chat は recent を絞るが summary は全面を返す。"""
    _seed_turn(client, source="nightly", n_rounds=1, called_propose_trade=0)
    _seed_turn(client, source="chat", n_rounds=1, called_propose_trade=0)

    body = client.get("/advisor/turns", params={"source": "chat"}).json()
    assert {t["source"] for t in body["recent"]} == {"chat"}
    # summary は絞らない（全母集団の比較が目的）。
    assert {row["source"] for row in body["summary"]} == {"chat", "nightly"}


def test_get_advisor_turns_empty(client: Any) -> None:
    """行が無ければ summary/recent とも空配列（500 にしない）。"""
    body = client.get("/advisor/turns").json()
    assert body == {"summary": [], "recent": []}
