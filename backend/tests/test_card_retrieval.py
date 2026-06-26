"""知識カードの意味検索・注入を検証（ADR-062 フェーズ2・ADR-045 同型）。

担保: repo.search_knowledge_cards が active のみ・余弦距離昇順・level フィルタ（手で BLOB 挿入）。
load_card_texts_for_injection が機能オフで全 active fallback、オンで ambient 常時＋chat は retrieval
追加・夜AI（query None）は ambient のみ。handle_search_cards が機能オフで reason 付き空。
sqlite-vec は engine が connect でロード（embedding は mock・ネット非依存）。
"""

from __future__ import annotations

import asyncio

import pytest

from app.advisor.tools import handlers
from app.db import repo
from app.db.engine import get_engine
from app.services import knowledge_cards as svc


def _add(
    title: str,
    *,
    status: str = "active",
    level: str | None = None,
    embedding: list[float] | None = None,
    always_inject: int = 0,
) -> int:
    cid = repo.insert_knowledge_card(
        title=title,
        body=f"body-{title}",
        when_to_apply=f"wta-{title}",
        status=status,
        level=level,
        always_inject=always_inject,
    )
    if embedding is not None:
        with get_engine().begin() as conn:
            repo.update_card_embedding(conn, cid, repo.pack_embedding(embedding), "m")
    return cid


@pytest.mark.usefixtures("temp_db")
def test_search_orders_by_distance_active_only() -> None:
    """距離昇順（近い順）で並び、active 以外・embedding なしは除外する。"""
    near = _add("near", embedding=[1.0, 0.0, 0.0])
    far = _add("far", embedding=[0.0, 1.0, 0.0])
    _add("draftcard", status="draft", embedding=[1.0, 0.0, 0.0])  # active でない→除外
    _add("noembed")  # embedding なし→除外
    qblob = repo.pack_embedding([1.0, 0.0, 0.0])
    with get_engine().connect() as conn:
        rows = repo.search_knowledge_cards(conn, qblob, limit=10)
    ids = [r["id"] for r in rows]
    assert ids[0] == near  # 最も近い
    assert set(ids) == {near, far}  # active＋embedded のみ


@pytest.mark.usefixtures("temp_db")
def test_search_level_filter() -> None:
    """level でフィルタする。"""
    stock = _add("stock1", level="stock", embedding=[1.0, 0.0])
    _add("market1", level="market", embedding=[1.0, 0.0])
    qblob = repo.pack_embedding([1.0, 0.0])
    with get_engine().connect() as conn:
        rows = repo.search_knowledge_cards(conn, qblob, level="stock")
    assert [r["id"] for r in rows] == [stock]


@pytest.mark.usefixtures("temp_db")
def test_injection_fallback_when_embedding_off() -> None:
    """embedding 機能オフは全 active を注入（フェーズ1 fallback・stock も含む）。"""
    _add("m1", level="market")
    _add("s1", level="stock")
    texts = asyncio.run(svc.load_card_texts_for_injection("半導体"))
    assert len(texts) == 2  # 機能オフは全 active


@pytest.mark.usefixtures("temp_db")
def test_injection_nightly_ambient_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """embedding オン・query None（夜AI）は ambient（market）のみ・stock は除外する。"""
    _add("m1", level="market", embedding=[1.0, 0.0])
    _add("s1", level="stock", embedding=[1.0, 0.0])
    monkeypatch.setattr(svc, "embedding_enabled", lambda: True)
    texts = asyncio.run(svc.load_card_texts_for_injection(None))
    assert len(texts) == 1
    assert "m1" in texts[0]


@pytest.mark.usefixtures("temp_db")
def test_injection_chat_adds_retrieval(monkeypatch: pytest.MonkeyPatch) -> None:
    """embedding オン・query あり（チャット）は ambient＋retrieval で stock も入る。"""
    _add("m1", level="market", embedding=[1.0, 0.0])
    _add("s_hit", level="stock", embedding=[1.0, 0.0])

    async def fake_embed(_texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0]]

    monkeypatch.setattr(svc, "embedding_enabled", lambda: True)
    monkeypatch.setattr(svc, "embed_texts", fake_embed)
    texts = asyncio.run(svc.load_card_texts_for_injection("query"))
    joined = "\n".join(texts)
    assert "m1" in joined  # ambient
    assert "s_hit" in joined  # retrieval で stock も入る


@pytest.mark.usefixtures("temp_db")
def test_handle_search_cards_off_returns_reason() -> None:
    """search_cards Tool は機能オフで items 空＋reason を返す（落とさない・ADR-018）。"""
    res = asyncio.run(handlers.handle_search_cards({"query": "x"}))
    assert res["items"] == []
    assert "reason" in res
