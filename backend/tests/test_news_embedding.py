"""ニュース意味検索 段階A（生成＋検索コア）を検証する（ADR-045）。

担保すること:
- repo: search_news が距離昇順・フィルタ・NULL 除外で効く（embedding を手で BLOB 挿入）。
  list_news_needing_embedding（未埋め込み/モデル不一致/summary 空除外）・update_news_embedding。
- service: search_news_corpus が機能オフ時 items 空＋reason／オン時（embed_texts mock）ランク返し。
  ingest_user_news の即時埋め込みが失敗しても貼付は成功する。
- job: embed_news が未設定で skip／設定時（mock）に null 行を埋める。機能有効なのに API 失敗なら
  ok=False（tag 系と契約対称・ADR-018・tasks/review-2026-06-12.md C-7。成功済み埋め込みは永続）。
- endpoint: GET /news/search が 200 で items を返す（embed_texts mock）。

本物の DB に触れず一時 SQLite（temp_db / client）で回す。embedding は mock（ネットに出ない）。
sqlite-vec は engine の connect リスナがロードする（実 vec_distance_cosine で距離検証）。
"""

from __future__ import annotations

import asyncio

from app.db import repo
from app.db.engine import get_engine
from app.db.schema import news, stocks


def _seed_stocks(*codes: str) -> None:
    """stock 層の news は code FK（stocks.code）を持つため、銘柄を先に入れる（FK 充足）。"""
    with get_engine().begin() as conn:
        for code in codes:
            conn.execute(stocks.insert().values(code=code, company_name=f"{code} 社"))


def _run(coro):  # noqa: ANN001, ANN202 — テスト専用の同期駆動（test_news_service と同流儀）
    """async サービスをテストから同期駆動する（pytest-asyncio 不使用・asyncio.run で回す）。"""
    return asyncio.run(coro)


def _insert_news(
    url: str,
    *,
    summary: str = "要約。",
    embedding: list[float] | None = None,
    embed_model: str | None = None,
    level: str = "market",
    code: str | None = None,
    sector17_code: str | None = None,
    published_at: str | None = "2026-06-05",
) -> int:
    """テスト用に news 1 行を直接 INSERT し id を返す（embedding は BLOB 化して入れる）。"""
    blob = repo.pack_embedding(embedding) if embedding is not None else None
    with get_engine().begin() as conn:
        result = conn.execute(
            news.insert().values(
                level=level,
                code=code,
                sector17_code=sector17_code,
                category="市況" if level == "market" else None,
                source="news",
                url=url,
                title=f"{url} のタイトル",
                summary=summary,
                published_at=published_at,
                fetched_at="2026-06-05T00:00:00+00:00",
                extraction_status="summarized",
                embedding=blob,
                embed_model=embed_model,
            )
        )
    return int(result.inserted_primary_key[0])


# ---------------------------------------------------------------------------
# repo
# ---------------------------------------------------------------------------


def test_pack_embedding_roundtrip() -> None:
    """pack_embedding が float32 LE BLOB を作る（vec_distance_cosine が読む格納形式・ADR-045）。"""
    import struct

    blob = repo.pack_embedding([1.0, 0.0, -0.5])
    assert blob == struct.pack("<3f", 1.0, 0.0, -0.5)


def test_search_news_orders_by_distance_and_excludes_null(temp_db) -> None:
    """search_news が余弦距離昇順（近い順）で並び、embedding NULL の行は除外する（ADR-045）。"""
    # クエリ [1,0,0] に近い順: near([1,0,0]・距離0) → mid([1,1,0]) → far([0,1,0]・距離1)。
    _insert_news("https://x/near", embedding=[1.0, 0.0, 0.0])
    _insert_news("https://x/mid", embedding=[1.0, 1.0, 0.0])
    _insert_news("https://x/far", embedding=[0.0, 1.0, 0.0])
    _insert_news("https://x/null", embedding=None)  # 除外される

    qblob = repo.pack_embedding([1.0, 0.0, 0.0])
    with get_engine().connect() as conn:
        rows = repo.search_news(conn, qblob, limit=10)

    urls = [r["url"] for r in rows]
    assert urls == ["https://x/near", "https://x/mid", "https://x/far"]  # NULL 除外＋距離昇順
    assert rows[0]["distance"] <= rows[1]["distance"] <= rows[2]["distance"]


def test_search_news_applies_filters(temp_db) -> None:
    """search_news が level/code/since/until でフィルタする（ADR-045）。"""
    _seed_stocks("7203", "6758")
    _insert_news(
        "https://x/m1", embedding=[1.0, 0.0, 0.0], level="market", published_at="2026-06-01"
    )
    _insert_news(
        "https://x/s1",
        embedding=[1.0, 0.0, 0.0],
        level="stock",
        code="7203",
        published_at="2026-06-05",
    )
    _insert_news(
        "https://x/s2",
        embedding=[1.0, 0.0, 0.0],
        level="stock",
        code="6758",
        published_at="2026-06-10",
    )

    qblob = repo.pack_embedding([1.0, 0.0, 0.0])
    with get_engine().connect() as conn:
        stock_rows = repo.search_news(conn, qblob, level="stock")
        code_rows = repo.search_news(conn, qblob, code="7203")
        range_rows = repo.search_news(conn, qblob, since="2026-06-04", until="2026-06-08")

    assert {r["url"] for r in stock_rows} == {"https://x/s1", "https://x/s2"}
    assert [r["url"] for r in code_rows] == ["https://x/s1"]
    assert [r["url"] for r in range_rows] == ["https://x/s1"]  # 06-05 のみ範囲内


def test_list_news_needing_embedding(temp_db) -> None:
    """未埋め込み/モデル不一致を返し、現行モデル一致と summary 空は除外する（ADR-045）。"""
    null_id = _insert_news("https://x/null", embedding=None)  # 未埋め込み → 対象
    stale_id = _insert_news("https://x/stale", embedding=[1.0, 0.0], embed_model="old")  # 不一致
    _insert_news("https://x/current", embedding=[1.0, 0.0], embed_model="m1")  # 一致 → 除外
    _insert_news("https://x/empty", summary="", embedding=None)  # summary 空 → 除外

    with get_engine().connect() as conn:
        rows = repo.list_news_needing_embedding(conn, current_model="m1", limit=10)

    assert {r["id"] for r in rows} == {null_id, stale_id}


def test_update_news_embedding(temp_db) -> None:
    """update_news_embedding が embedding/embed_model/embedded_at を更新する（ADR-045）。"""
    nid = _insert_news("https://x/u", embedding=None)
    with get_engine().begin() as conn:
        repo.update_news_embedding(conn, nid, repo.pack_embedding([0.1, 0.2]), "m1")
    with get_engine().connect() as conn:
        row = conn.execute(news.select().where(news.c.id == nid)).mappings().first()
    assert row is not None
    assert row["embed_model"] == "m1"
    assert row["embedded_at"] is not None
    assert row["embedding"] == repo.pack_embedding([0.1, 0.2])


# ---------------------------------------------------------------------------
# service
# ---------------------------------------------------------------------------


def test_search_news_corpus_off_returns_reason(temp_db, monkeypatch) -> None:
    """embedding 機能オフなら items 空＋reason を返す（ADR-006/018）。"""
    from app.services import news as news_svc

    monkeypatch.setattr(news_svc, "embedding_enabled", lambda: False)
    with get_engine().connect() as conn:
        result = _run(news_svc.search_news_corpus(conn, "利上げ観測"))
    assert result["items"] == []
    assert "機能オフ" in result["reason"]


def test_search_news_corpus_ranks_on(temp_db, monkeypatch) -> None:
    """機能オン時（embed_texts mock）に距離昇順でランクして返す（ADR-045）。"""
    from app.services import news as news_svc

    _insert_news("https://x/near", embedding=[1.0, 0.0, 0.0])
    _insert_news("https://x/far", embedding=[0.0, 1.0, 0.0])

    monkeypatch.setattr(news_svc, "embedding_enabled", lambda: True)

    async def _fake_embed(texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] for _ in texts]

    monkeypatch.setattr(news_svc, "embed_texts", _fake_embed)
    with get_engine().connect() as conn:
        result = _run(news_svc.search_news_corpus(conn, "クエリ", limit=5))

    assert [i["url"] for i in result["items"]] == ["https://x/near", "https://x/far"]
    assert "distance" in result["items"][0]


def test_ingest_user_news_immediate_embed_failure_still_saves(temp_db, monkeypatch) -> None:
    """即時埋め込みが失敗しても貼付（ingest）は成功する（ADR-045 best-effort）。"""
    from app.services import news as news_svc

    async def _fake_summarize(text: str) -> str:
        return "要約済み。"

    monkeypatch.setattr(news_svc, "summarize_article", _fake_summarize)
    monkeypatch.setattr(news_svc, "embedding_enabled", lambda: True)
    monkeypatch.setattr(news_svc, "embedding_model", lambda: "m1")

    async def _boom(texts: list[str]) -> list[list[float]]:
        raise RuntimeError("embeddings API down")

    monkeypatch.setattr(news_svc, "embed_texts", _boom)

    saved = _run(news_svc.ingest_user_news(text="本文テキスト", code=None))
    assert saved["summary"] == "要約済み。"  # 貼付は成功
    # 埋め込みは失敗したので embedding は NULL のまま（夜ジョブが後で拾う）。
    with get_engine().connect() as conn:
        row = conn.execute(news.select().where(news.c.id == saved["id"])).mappings().first()
    assert row is not None
    assert row["embedding"] is None


# ---------------------------------------------------------------------------
# job
# ---------------------------------------------------------------------------


def test_embed_news_skips_when_disabled(temp_db, monkeypatch) -> None:
    """embedding 未設定なら静かに skip（ok=True・rows=0・ADR-006）。"""
    from app.batch.jobs import embed_news

    monkeypatch.setattr(embed_news, "embedding_enabled", lambda: False)
    result = embed_news.run()
    assert result.ok is True
    assert result.rows == 0
    assert "skip" in result.detail


def test_embed_news_fills_null_rows(temp_db, monkeypatch) -> None:
    """全バッチ成功時は ok=True で未埋め込み行を埋める（ADR-045・review-2026-06-12 C-7）。"""
    from app.batch.jobs import embed_news

    n1 = _insert_news("https://x/a", embedding=None)
    n2 = _insert_news("https://x/b", embedding=None)

    monkeypatch.setattr(embed_news, "embedding_enabled", lambda: True)
    monkeypatch.setattr(embed_news, "embedding_model", lambda: "m1")

    async def _fake_embed(texts: list[str]) -> list[list[float]]:
        return [[0.5, 0.5] for _ in texts]

    monkeypatch.setattr(embed_news, "embed_texts", _fake_embed)
    result = embed_news.run()

    assert result.ok is True
    assert result.rows == 2
    with get_engine().connect() as conn:
        rows = {r["id"]: r for r in conn.execute(news.select()).mappings().all()}
    assert rows[n1]["embedding"] is not None
    assert rows[n1]["embed_model"] == "m1"
    assert rows[n2]["embedding"] is not None


def test_embed_news_failed_batch_returns_not_ok(temp_db, monkeypatch) -> None:
    """機能有効なのに API 失敗なら ok=False（tag 系と契約対称・ADR-018・review-2026-06-12 C-7）。

    ok=True のままだと runner の通知に乗らず意味検索が静かに陳腐化するため、
    failed_batches > 0 で ok=False に倒すことを担保する。
    """
    from app.batch.jobs import embed_news

    _insert_news("https://x/fail", embedding=None)

    monkeypatch.setattr(embed_news, "embedding_enabled", lambda: True)
    monkeypatch.setattr(embed_news, "embedding_model", lambda: "m1")

    async def _boom(texts: list[str]) -> list[list[float]]:
        raise RuntimeError("embeddings API down")

    monkeypatch.setattr(embed_news, "embed_texts", _boom)
    result = embed_news.run()

    assert result.ok is False
    assert result.rows == 0
    assert "バッチ失敗" in result.detail


def test_embed_news_partial_success_persists_then_not_ok(temp_db, monkeypatch) -> None:
    """途中失敗でも成功済み埋め込みは永続し ok=False（自己回復性維持・ADR-018・C-7）。

    1 バッチ目成功→2 バッチ目失敗のとき、成功分の embedding は残り（翌晩は未埋め込み分だけ
    再試行される）、結果は ok=False で通知に乗ることを担保する。
    """
    from app.batch.jobs import embed_news

    _insert_news("https://x/a", embedding=None)
    _insert_news("https://x/b", embedding=None)

    monkeypatch.setattr(embed_news, "embedding_enabled", lambda: True)
    monkeypatch.setattr(embed_news, "embedding_model", lambda: "m1")
    monkeypatch.setattr(embed_news, "EMBED_BATCH", 1)  # 2 バッチに分割させる

    calls = {"n": 0}

    async def _fail_on_second(texts: list[str]) -> list[list[float]]:
        calls["n"] += 1
        if calls["n"] >= 2:
            raise RuntimeError("embeddings API down")
        return [[0.5, 0.5] for _ in texts]

    monkeypatch.setattr(embed_news, "embed_texts", _fail_on_second)
    result = embed_news.run()

    assert result.ok is False
    assert result.rows == 1  # 1 バッチ目の成功分は数えられている
    with get_engine().connect() as conn:
        rows = conn.execute(news.select()).mappings().all()
    embedded = [r for r in rows if r["embedding"] is not None]
    assert len(embedded) == 1  # 成功済み埋め込みは rollback されず永続


# ---------------------------------------------------------------------------
# endpoint
# ---------------------------------------------------------------------------


def test_get_news_search_endpoint(client, monkeypatch) -> None:
    """GET /news/search が 200 で items を返す（embed_texts mock・ADR-045）。"""
    from app.services import news as news_svc

    _insert_news("https://x/near", embedding=[1.0, 0.0, 0.0])
    _insert_news("https://x/far", embedding=[0.0, 1.0, 0.0])

    monkeypatch.setattr(news_svc, "embedding_enabled", lambda: True)

    async def _fake_embed(texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] for _ in texts]

    monkeypatch.setattr(news_svc, "embed_texts", _fake_embed)

    resp = client.get("/news/search", params={"q": "クエリ", "limit": 5})
    assert resp.status_code == 200
    body = resp.json()
    urls = [i["url"] for i in body["items"]]
    assert urls == ["https://x/near", "https://x/far"]
    assert body["reason"] is None


def test_get_news_search_off_returns_reason(client, monkeypatch) -> None:
    """機能オフ時、GET /news/search は 200 で items 空＋reason を返す（ADR-018）。"""
    from app.services import news as news_svc

    monkeypatch.setattr(news_svc, "embedding_enabled", lambda: False)
    resp = client.get("/news/search", params={"q": "クエリ"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["reason"] is not None
