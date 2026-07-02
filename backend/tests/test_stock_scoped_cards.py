"""知識カードの銘柄スコープ（market/code）を検証（ADR-062 追補・銘柄粒度の知識軸）。

設計の真実: tasks/stock-notes-design.md（§4 受け入れテスト 13 項目）・
docs/decisions.md ADR-062 追補。

担保:
- repo.list_active_cards_by_codes が exact-match（active・weight 降順・embedding 不要）。
- repo.search_knowledge_cards(only_unscoped=True) が銘柄ノート（code 付き）を除外（汎用プール）。
- load_card_texts_for_injection の chat（focus_code）／夜AI（candidate_codes）exact-match 注入と
  他銘柄漏れ防止、ambient/dedup の維持。
- search_cards Tool が code 指定で exact-match。
- persist_card_ops_from_tool_runs（propose_card）が code→market 解決・未知 drop・level='stock'。
- POST/PUT /cards の code 実在検証（未知 400）・level='stock' 矯正・always_inject 禁止・
  付け替え/除去。

一時 SQLite（temp_db は create_schema・client は alembic）。embedding は mock（ネット非依存）。
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.advisor.card_triage import AssistResult
from app.advisor.journaling import persist_card_ops_from_tool_runs
from app.advisor.tools import handlers
from app.db import repo
from app.db.engine import get_engine
from app.services import knowledge_cards as svc


def _add(
    title: str,
    *,
    status: str = "active",
    level: str | None = None,
    market: str | None = None,
    code: str | None = None,
    embedding: list[float] | None = None,
    always_inject: int = 0,
    weight: float = 1.0,
) -> int:
    """テスト用カード投入（code/market 付き・embedding は任意）。"""
    cid = repo.insert_knowledge_card(
        title=title,
        body=f"body-{title}",
        when_to_apply=f"wta-{title}",
        status=status,
        level=level,
        market=market,
        code=code,
        always_inject=always_inject,
        weight=weight,
    )
    if embedding is not None:
        with get_engine().begin() as conn:
            repo.update_card_embedding(conn, cid, repo.pack_embedding(embedding), "m")
    return cid


def _seed_jp_stock(code: str) -> None:
    repo.upsert_stocks(
        [
            {
                "code": code,
                "company_name": f"会社{code}",
                "sector33_code": "3700",
                "sector17_code": "6",
                "market_code": "0111",
                "is_etf": 0,
                "updated_at": "2026-07-02T00:00:00+00:00",
            }
        ]
    )


# --- repo 層 ----------------------------------------------------------------


@pytest.mark.usefixtures("temp_db")
def test_list_active_cards_by_codes_exact_weight_desc() -> None:
    """#1: 指定 code の active ノートを weight 降順で返す（draft/他 code は除外・
    embedding 不要）。"""
    low = _add("low", market="JP", code="72030", weight=1.0)
    high = _add("high", market="JP", code="72030", weight=5.0)
    _add("draftnote", status="draft", market="JP", code="72030")  # active でない → 除外
    _add("other", market="JP", code="67580")  # 別 code → 除外
    with get_engine().connect() as conn:
        rows = repo.list_active_cards_by_codes(conn, codes=["72030"])
    assert [r["id"] for r in rows] == [high, low]  # weight 降順・embedding なしでも返る


@pytest.mark.usefixtures("temp_db")
def test_search_only_unscoped_excludes_stock_notes() -> None:
    """#2: only_unscoped=True は銘柄ノート（code 付き）を除外（汎用の意味検索プール）。"""
    unscoped = _add("market1", level="market", embedding=[1.0, 0.0])
    _add("stocknote", level="stock", market="JP", code="72030", embedding=[1.0, 0.0])
    qblob = repo.pack_embedding([1.0, 0.0])
    with get_engine().connect() as conn:
        only = repo.search_knowledge_cards(conn, qblob, only_unscoped=True, limit=10)
        both = repo.search_knowledge_cards(conn, qblob, only_unscoped=False, limit=10)
    assert [r["id"] for r in only] == [unscoped]  # 銘柄ノートは除外
    assert len(both) == 2  # 後方互換: 既定は除外しない


# --- 注入経路（service）------------------------------------------------------


@pytest.mark.usefixtures("temp_db")
def test_injection_focus_code_injects_stock_note(monkeypatch: pytest.MonkeyPatch) -> None:
    """#4: chat は focus.code の銘柄ノートを意味距離を問わず無条件注入（③(1)）。"""
    _add("note6920", level="stock", market="JP", code="69200")  # embedding なしでも入る

    async def fake_embed(_texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0]]

    monkeypatch.setattr(svc, "embedding_enabled", lambda: True)
    monkeypatch.setattr(svc, "embed_texts", fake_embed)
    texts = asyncio.run(svc.load_card_texts_for_injection("何か", focus_code="69200"))
    assert any("note6920" in t for t in texts)


@pytest.mark.usefixtures("temp_db")
def test_injection_other_focus_excludes_stock_note(monkeypatch: pytest.MonkeyPatch) -> None:
    """#5: 別銘柄を見ているとき、その銘柄ノートは注入されない（漏れ防止・③(2)）。

    69200 のノートに query と近い embedding を持たせても、focus が 72030 なら exact-match に乗らず、
    汎用の意味検索プールからも除外される（code 付きは only_unscoped で落ちる）。
    """
    _add("note6920", level="stock", market="JP", code="69200", embedding=[1.0, 0.0])

    async def fake_embed(_texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0]]  # 69200 ノートと同一ベクトル（意味的には最も近い）

    monkeypatch.setattr(svc, "embedding_enabled", lambda: True)
    monkeypatch.setattr(svc, "embed_texts", fake_embed)
    texts = asyncio.run(svc.load_card_texts_for_injection("何か", focus_code="72030"))
    assert not any("note6920" in t for t in texts)  # 他銘柄のノートは漏れない


@pytest.mark.usefixtures("temp_db")
def test_injection_nightly_candidate_codes(monkeypatch: pytest.MonkeyPatch) -> None:
    """#6: 夜AIは注目候補の code ぶんのノートを注入・候補外の code は注入しない（④）。"""
    _add("hit", level="stock", market="JP", code="69200")
    _add("miss", level="stock", market="JP", code="99990")  # 候補外
    monkeypatch.setattr(svc, "embedding_enabled", lambda: True)
    texts = asyncio.run(svc.load_card_texts_for_injection(None, candidate_codes=["69200", "13010"]))
    joined = "\n".join(texts)
    assert "hit" in joined
    assert "miss" not in joined


@pytest.mark.usefixtures("temp_db")
def test_injection_ambient_and_dedup_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    """#3: ambient(always_inject 非銘柄)＋exact-match(focus)＋意味検索(非銘柄) を id で dedup。"""
    _add("ambient", always_inject=1, embedding=[0.0, 1.0])  # 非銘柄・常時
    _add("stocknote", level="stock", market="JP", code="69200")  # focus で exact-match
    _add("hit", embedding=[1.0, 0.0])  # 非銘柄・意味検索で当たる

    async def fake_embed(_texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0]]

    monkeypatch.setattr(svc, "embedding_enabled", lambda: True)
    monkeypatch.setattr(svc, "embed_texts", fake_embed)
    texts = asyncio.run(svc.load_card_texts_for_injection("query", focus_code="69200"))
    joined = "\n".join(texts)
    assert "ambient" in joined  # always_inject（非銘柄）
    assert "stocknote" in joined  # focus の exact-match
    assert "hit" in joined  # 意味検索（非銘柄）
    assert len(texts) == len(set(texts))  # 同一カードが二重注入されない（id dedup）


# --- search_cards Tool ------------------------------------------------------


@pytest.mark.usefixtures("temp_db")
def test_handle_search_cards_code_exact_match() -> None:
    """#13: search_cards Tool は code 指定でその銘柄の active ノートを exact-match で返す。"""
    _add("note", level="stock", market="JP", code="69200")  # embedding なしでも返る
    res = asyncio.run(handlers.handle_search_cards({"query": "使わない", "code": "69200"}))
    assert [i["code"] for i in res["items"]] == ["69200"]


# --- persister（propose_card）-----------------------------------------------


@pytest.mark.usefixtures("temp_db")
def test_persist_propose_card_with_code_creates_stock_note() -> None:
    """#9: code 付き propose_card は market 解決＋level='stock' で draft 起票する。"""
    _seed_jp_stock("69200")
    runs = [{"name": "propose_card", "args": {"body": "決算後に戻る癖", "code": "69200"}}]
    with get_engine().begin() as conn:
        result = persist_card_ops_from_tool_runs(conn, tool_runs=runs, date="2026-07-02")
    assert len(result["cards"]) == 1
    with get_engine().connect() as conn:
        card = repo.get_knowledge_card(conn, result["cards"][0])
    assert card is not None
    assert card["code"] == "69200"
    assert card["market"] == "JP"
    assert card["level"] == "stock"
    assert card["status"] == "draft"


@pytest.mark.usefixtures("temp_db")
def test_persist_propose_card_unknown_code_dropped() -> None:
    """#9: 未知 code の propose_card は起票せず drop（幻覚を queue に入れない・ADR-018）。"""
    runs = [{"name": "propose_card", "args": {"body": "本文", "code": "99999"}}]
    with get_engine().begin() as conn:
        result = persist_card_ops_from_tool_runs(conn, tool_runs=runs, date="2026-07-02")
    assert result["cards"] == []


# --- API（POST/PUT /cards）--------------------------------------------------


@pytest.fixture(autouse=True)
def _assist_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    """既定は「AI 未整形」（assist_card→None）＝client 経路の create がネットに出ないための安全網。

    level 上書きを見るテストは本文内で assist_card を上書きする（test_cards_api と同型・後勝ち）。
    """

    async def _none(**_kwargs: object) -> AssistResult | None:
        return None

    monkeypatch.setattr("app.advisor.card_triage.assist_card", _none)


def test_create_card_with_code_sets_stock_level(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#7/#10: code 付き POST /cards は実在検証し market 解決＋level='stock' に矯正
    （assist より優先）。"""

    async def _fake(**_kwargs: object) -> AssistResult | None:
        return AssistResult(
            title="AI 見出し",
            when_to_apply="状況",
            level="market",  # code があれば router が 'stock' に上書き（#10）
            verdict="active",
            reason="r",
            quant_note=None,
        )

    monkeypatch.setattr("app.advisor.card_triage.assist_card", _fake)
    _seed_jp_stock("69200")
    res = client.post("/cards", json={"body": "決算後ドリフト", "code": "69200"})
    assert res.status_code == 201
    c = res.json()
    assert c["code"] == "69200"
    assert c["market"] == "JP"
    assert c["level"] == "stock"  # assist の 'market' を上書き
    assert c["always_inject"] is False  # 銘柄ノートは always_inject 禁止（#8）


def test_create_card_unknown_code_400(client: TestClient) -> None:
    """#7: 未知 code の POST /cards は 400（死にノートを作らない）。"""
    res = client.post("/cards", json={"body": "本文", "code": "99999"})
    assert res.status_code == 400


def test_update_card_set_and_clear_code(client: TestClient) -> None:
    """#11: PUT /cards で code を付け替え（level='stock'）→ 除去（汎用プールへ戻る）。"""
    _seed_jp_stock("69200")
    cid = client.post("/cards", json={"body": "本文"}).json()["id"]  # 非銘柄で作成
    # 銘柄化
    set_res = client.put(f"/cards/{cid}", json={"code": "69200"})
    assert set_res.status_code == 200
    c = set_res.json()
    assert c["code"] == "69200"
    assert c["market"] == "JP"
    assert c["level"] == "stock"
    # 銘柄解除（code を空に）
    clr = client.put(f"/cards/{cid}", json={"code": ""}).json()
    assert clr["code"] is None
    assert clr["market"] is None
    assert clr["level"] is None


def test_update_card_unknown_code_400(client: TestClient) -> None:
    """#11: PUT /cards で未知 code を付けようとすると 400。"""
    cid = client.post("/cards", json={"body": "本文"}).json()["id"]
    assert client.put(f"/cards/{cid}", json={"code": "99999"}).status_code == 400
