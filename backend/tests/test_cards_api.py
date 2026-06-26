"""/cards エンドポイントの CRUD・activate・triage を検証（ADR-062・backend-router-pattern）。

担保: POST が draft で作成・GET/一覧/404・PUT 更新・DELETE・activate（人間承認で active）・triage
（モックした AI 審査の verdict で status を振り分け、'active' は draft 据え置きで人間承認待ち）。
client（一時 SQLite＋alembic）で結合検証。AI 審査 LLM はモックしネットに出ない。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.advisor.card_triage import TriageResult


def test_create_returns_draft_and_get(client: TestClient) -> None:
    """POST で draft 作成、GET で取得できる。"""
    res = client.post(
        "/cards",
        json={"title": "東証テーマ", "body": "本文", "when_to_apply": "条件", "level": "market"},
    )
    assert res.status_code == 201
    card = res.json()
    assert card["status"] == "draft"
    assert card["level"] == "market"
    cid = card["id"]

    got = client.get(f"/cards/{cid}")
    assert got.status_code == 200
    assert got.json()["title"] == "東証テーマ"


def test_get_missing_404(client: TestClient) -> None:
    """存在しない id は 404。"""
    assert client.get("/cards/99999").status_code == 404


def test_list_status_filter(client: TestClient) -> None:
    """status で一覧を絞り込める。"""
    client.post("/cards", json={"title": "d", "body": "b"})
    cid = client.post("/cards", json={"title": "a", "body": "b"}).json()["id"]
    client.post(f"/cards/{cid}/activate")
    assert len(client.get("/cards").json()) == 2
    actives = client.get("/cards", params={"status": "active"}).json()
    assert len(actives) == 1
    assert actives[0]["id"] == cid


def test_update(client: TestClient) -> None:
    """PUT で本文と always_inject を更新できる。"""
    cid = client.post("/cards", json={"title": "t", "body": "b"}).json()["id"]
    res = client.put(f"/cards/{cid}", json={"body": "b2", "always_inject": True})
    assert res.status_code == 200
    card = res.json()
    assert card["body"] == "b2"
    assert card["always_inject"] is True


def test_delete(client: TestClient) -> None:
    """DELETE すると 404 になる。"""
    cid = client.post("/cards", json={"title": "t", "body": "b"}).json()["id"]
    assert client.delete(f"/cards/{cid}").status_code == 204
    assert client.get(f"/cards/{cid}").status_code == 404


def test_activate(client: TestClient) -> None:
    """activate で status が active になる（人間承認）。"""
    cid = client.post("/cards", json={"title": "t", "body": "b"}).json()["id"]
    res = client.post(f"/cards/{cid}/activate")
    assert res.status_code == 200
    assert res.json()["status"] == "active"


def test_triage_needs_quant_applies_status(client: TestClient, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """AI 審査 verdict=needs_quant なら status と quant_note を反映する。"""

    async def fake(**_kwargs: object) -> TriageResult:
        return TriageResult(
            verdict="needs_quant",
            reason="新指標が要る",
            quant_note="X を計算",
            linked_signal_type=None,
        )

    monkeypatch.setattr("app.advisor.card_triage.triage_card", fake)
    cid = client.post("/cards", json={"title": "t", "body": "b"}).json()["id"]
    res = client.post(f"/cards/{cid}/triage")
    assert res.status_code == 200
    body = res.json()
    assert body["triage"]["verdict"] == "needs_quant"
    assert body["card"]["status"] == "needs_quant"
    assert body["card"]["quant_note"] == "X を計算"


def test_triage_active_keeps_draft_for_human(client: TestClient, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """verdict=active は draft のまま（人間承認待ち）・linked_signal_type だけ反映する。"""

    async def fake(**_kwargs: object) -> TriageResult:
        return TriageResult(
            verdict="active",
            reason="既存 signal の読み方",
            quant_note=None,
            linked_signal_type="momentum",
        )

    monkeypatch.setattr("app.advisor.card_triage.triage_card", fake)
    cid = client.post("/cards", json={"title": "t", "body": "b"}).json()["id"]
    body = client.post(f"/cards/{cid}/triage").json()
    assert body["triage"]["verdict"] == "active"
    assert body["card"]["status"] == "draft"  # active 化は人間承認（ADR-009）
    assert body["card"]["linked_signal_type"] == "momentum"


def test_triage_none_keeps_status(client: TestClient, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """AI 審査が None（面未設定/応答不正）なら status 据え置き・triage=None。"""

    async def fake(**_kwargs: object) -> None:
        return None

    monkeypatch.setattr("app.advisor.card_triage.triage_card", fake)
    cid = client.post("/cards", json={"title": "t", "body": "b"}).json()["id"]
    body = client.post(f"/cards/{cid}/triage").json()
    assert body["triage"] is None
    assert body["card"]["status"] == "draft"
