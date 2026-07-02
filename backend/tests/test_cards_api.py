"""/cards の CRUD・activate・追加時 auto-triage を検証（ADR-062 追補・雑追加リデザイン）。

担保（リデザイン後）:
- POST /cards は本文（＋source）だけで作り、追加時に同期で `assist_card`（triage 面）を走らせ
  verdict を status へ反映（rejected/to_core/needs_quant は自動・active 候補は draft 留置）。
- title は AI 生成（先頭切り出しはしない）。AI 未整形（None）でも本文は draft 保存し title="".
- triage の reason は triage_reason 列に永続（再読込後も残る）。
- POST /cards/{id}/assist は既存カードを再整形（未整形の再試行＋編集後の再審査）。active は draft。
- 旧 `POST /cards/assist`（preview）と `POST /cards/{id}/triage`（verdict only）は撤去。

client（一時 SQLite＋alembic）で結合検証。AI（assist_card）は必ずモックしネットに出ない
（conftest が triage 面もダミー provider で seed＝未モックだと generate_once が外部に出るため）。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.advisor.card_triage import AssistResult


@pytest.fixture(autouse=True)
def _assist_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    """既定は「AI 未整形」（assist_card→None）。テストがネットに出ないための安全網。

    verdict 別の挙動を見るテストは各自 _mock_assist で上書きする（後勝ち）。
    """

    async def _none(**_kwargs: object) -> AssistResult | None:
        return None

    monkeypatch.setattr("app.advisor.card_triage.assist_card", _none)


def _mock_assist(monkeypatch: pytest.MonkeyPatch, result: AssistResult | None) -> None:
    async def _fake(**_kwargs: object) -> AssistResult | None:
        return result

    monkeypatch.setattr("app.advisor.card_triage.assist_card", _fake)


# --- 追加（auto-triage） ----------------------------------------------------


def test_create_active_candidate_stays_draft_with_reason(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """本文だけ POST → AI が title/level 生成。verdict=active は draft 留置・reason/linked 反映。"""
    _mock_assist(
        monkeypatch,
        AssistResult(
            title="AI 見出し",
            when_to_apply="この状況で効く",
            level="market",
            verdict="active",
            reason="既存値で成立",
            quant_note=None,
        ),
    )
    res = client.post("/cards", json={"body": "本文", "source": "https://example.com/x"})
    assert res.status_code == 201
    c = res.json()
    assert c["title"] == "AI 見出し"
    assert c["level"] == "market"
    assert c["status"] == "draft"  # active 候補は人間承認待ち（ADR-009）
    assert c["triage_reason"] == "既存値で成立"
    assert c["source"] == "https://example.com/x"


def test_create_rejected_sets_status(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """verdict=rejected は status=rejected を自動反映し reason を残す。"""
    _mock_assist(
        monkeypatch,
        AssistResult(
            title="T",
            when_to_apply=None,
            level=None,
            verdict="rejected",
            reason="一般常識でカード不要",
            quant_note=None,
        ),
    )
    c = client.post("/cards", json={"body": "PER は 15 倍が目安"}).json()
    assert c["status"] == "rejected"
    assert c["triage_reason"] == "一般常識でカード不要"


def test_create_needs_quant_sets_status_and_note(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """verdict=needs_quant は status と quant_note・reason を反映。"""
    _mock_assist(
        monkeypatch,
        AssistResult(
            title="T",
            when_to_apply=None,
            level=None,
            verdict="needs_quant",
            reason="新指標が要る",
            quant_note="X を計算",
        ),
    )
    c = client.post("/cards", json={"body": "b"}).json()
    assert c["status"] == "needs_quant"
    assert c["quant_note"] == "X を計算"
    assert c["triage_reason"] == "新指標が要る"


def test_create_graceful_when_ai_unavailable(client: TestClient) -> None:
    """AI 未整形（None）でも本文は draft 保存・title 空（先頭切り出しなし）・reason None。"""
    res = client.post("/cards", json={"body": "本文だけ\n2 行目"})
    assert res.status_code == 201
    c = res.json()
    assert c["body"].startswith("本文だけ")
    assert c["title"] == ""  # 本文先頭で代替しない（ADR-062 追補）
    assert c["status"] == "draft"
    assert c["triage_reason"] is None


def test_create_then_get(client: TestClient) -> None:
    """作成（AI 未整形）したカードを GET で取得できる。"""
    cid = client.post("/cards", json={"body": "本文"}).json()["id"]
    got = client.get(f"/cards/{cid}")
    assert got.status_code == 200
    assert got.json()["body"] == "本文"


# --- 再整形（POST /cards/{id}/assist） --------------------------------------


def test_reassist_updates_and_keeps_active_as_draft(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AI 未整形カードを再整形 → title 等が付く。active は draft 維持（自動 activate なし）。"""
    cid = client.post("/cards", json={"body": "本文"}).json()["id"]  # autouse None で AI 未整形
    _mock_assist(
        monkeypatch,
        AssistResult(
            title="後から付いた見出し",
            when_to_apply="状況",
            level="market",
            verdict="active",
            reason="採用",
            quant_note=None,
        ),
    )
    res = client.post(f"/cards/{cid}/assist")
    assert res.status_code == 200
    body = res.json()
    assert body["triage"]["verdict"] == "active"
    assert body["card"]["title"] == "後から付いた見出し"
    assert body["card"]["status"] == "draft"  # active 化は人間承認（ADR-009）
    assert body["card"]["triage_reason"] == "採用"


def test_reassist_active_card_is_not_demoted(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#3: 既に active（人間承認済み）のカードを再整形しても draft に降格しない（active 温存）。"""
    cid = client.post("/cards", json={"body": "本文"}).json()["id"]  # autouse None → draft
    client.post(f"/cards/{cid}/activate")  # 人間が承認 → active
    assert client.get(f"/cards/{cid}").json()["status"] == "active"
    _mock_assist(
        monkeypatch,
        AssistResult(
            title="整えた見出し",
            when_to_apply="状況",
            level="market",
            verdict="active",  # AI は再び「active（既存データで成立）」と判定
            reason="既存データで成立",
            quant_note=None,
        ),
    )
    body = client.post(f"/cards/{cid}/assist").json()
    assert body["card"]["title"] == "整えた見出し"  # 整形は効く
    assert body["card"]["status"] == "active"  # active は温存（draft へ降格しない）


def test_create_runs_immediate_embedding_on_async_path(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#9: POST /cards（async）の即時埋め込みが実際に走る（asyncio.run を実行中ループで呼ばない）。

    旧実装は sync 版が内部で asyncio.run を呼び、実行中イベントループ上で RuntimeError → 握り潰しで
    埋め込みが 100% 死んでいた（201 は返るが embedding=NULL）。埋め込みが実際に走ったかで判定する。
    """
    calls: list[list[str]] = []

    async def _fake_embed(texts: list[str]) -> list[list[float]]:
        calls.append(list(texts))
        return [[0.1, 0.2, 0.3]]

    monkeypatch.setattr("app.batch.jobs.embed_cards.embedding_enabled", lambda: True)
    monkeypatch.setattr("app.batch.jobs.embed_cards.embedding_model", lambda: "fake-model")
    monkeypatch.setattr("app.batch.jobs.embed_cards.embed_texts", _fake_embed)

    res = client.post("/cards", json={"body": "本文だけの知識"})
    assert res.status_code == 201
    assert calls  # 埋め込みが実際に走った（旧経路は RuntimeError で走らなかった）


def test_reassist_none_keeps_status(client: TestClient) -> None:
    """再整形でも AI が None なら status 据え置き・triage=None（autouse None）。"""
    cid = client.post("/cards", json={"body": "本文"}).json()["id"]
    body = client.post(f"/cards/{cid}/assist").json()
    assert body["triage"] is None
    assert body["card"]["status"] == "draft"


def test_reassist_missing_404(client: TestClient) -> None:
    """存在しない id の再整形は 404。"""
    assert client.post("/cards/99999/assist").status_code == 404


# --- CRUD（従来機能・本文だけで作る） ---------------------------------------


def test_get_missing_404(client: TestClient) -> None:
    assert client.get("/cards/99999").status_code == 404


def test_list_status_filter(client: TestClient) -> None:
    """status で一覧を絞り込める（AI 未整形 draft × 2、1 つを activate）。"""
    client.post("/cards", json={"body": "b1"})
    cid = client.post("/cards", json={"body": "b2"}).json()["id"]
    client.post(f"/cards/{cid}/activate")
    assert len(client.get("/cards").json()) == 2
    actives = client.get("/cards", params={"status": "active"}).json()
    assert len(actives) == 1
    assert actives[0]["id"] == cid


def test_update_body_and_always_inject(client: TestClient) -> None:
    cid = client.post("/cards", json={"body": "b"}).json()["id"]
    res = client.put(f"/cards/{cid}", json={"body": "b2", "always_inject": True})
    assert res.status_code == 200
    card = res.json()
    assert card["body"] == "b2"
    assert card["always_inject"] is True


def test_update_weight(client: TestClient) -> None:
    cid = client.post("/cards", json={"body": "b"}).json()["id"]
    res = client.put(f"/cards/{cid}", json={"weight": 2.0})
    assert res.status_code == 200
    assert res.json()["weight"] == 2.0


def test_delete(client: TestClient) -> None:
    cid = client.post("/cards", json={"body": "b"}).json()["id"]
    assert client.delete(f"/cards/{cid}").status_code == 204
    assert client.get(f"/cards/{cid}").status_code == 404


def test_activate(client: TestClient) -> None:
    """activate で status が active になる（人間承認）。"""
    cid = client.post("/cards", json={"body": "b"}).json()["id"]
    res = client.post(f"/cards/{cid}/activate")
    assert res.status_code == 200
    assert res.json()["status"] == "active"


# --- 撤去したエンドポイント -------------------------------------------------


def test_removed_endpoints_gone(client: TestClient) -> None:
    """旧 preview /cards/assist と verdict-only /cards/{id}/triage は撤去済み。"""
    assert client.post("/cards/assist", json={"body": "b"}).status_code in (404, 405)
    cid = client.post("/cards", json={"body": "b"}).json()["id"]
    assert client.post(f"/cards/{cid}/triage").status_code in (404, 405)
