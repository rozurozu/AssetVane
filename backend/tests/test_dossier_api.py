"""ドシエ REST API テスト（GET /dossiers/{code}・POST .../investigate・spec §5.2）。

`client` フィクスチャ（alembic 経路で一時 SQLite）で叩く。investigate_stock は監視せず
モックして「DB に書いた体」にする（LLM/外部 fetch_news は呼ばない＝spec §8 テスト計画）。
検証対象:
- 未調査時の挙動（空ドシエ 200・last_investigated_at=None）。
- 合成（sources が JOIN で乗る・key_facts が obj になる）。
- POST investigate が同期で最新ドシエを返す（投資後の dossier を返却）。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from app.db import repo
from app.db.engine import get_engine

STOCK_A = {"code": "7203", "company_name": "トヨタ自動車"}


def test_get_dossier_uninvestigated_returns_empty(client: Any) -> None:
    """未調査銘柄は空ドシエ（summary_md=""・sources=[]・last=None）を 200 で返す（spec §5.2）。"""
    repo.upsert_stocks([STOCK_A])
    res = client.get("/dossiers/7203")
    assert res.status_code == 200
    body = res.json()
    assert body["code"] == "7203"
    assert body["summary_md"] == ""
    assert body["key_facts"] is None
    assert body["last_investigated_at"] is None
    assert body["sources"] == []


def test_get_dossier_composes_sources_and_key_facts(client: Any) -> None:
    """合成（sources JOIN）・key_facts が obj・sources は published_at 降順（spec §5.2）。"""
    repo.upsert_stocks([STOCK_A])
    now = datetime.now(UTC).isoformat()
    with get_engine().begin() as conn:
        repo.upsert_dossier(
            conn,
            code="7203",
            summary_md="# トヨタ\n好調",
            key_facts=json.dumps({"per": 10.5, "topic": "増配"}, ensure_ascii=False),
            last_investigated_at=now,
            updated_at=now,
        )
        repo.upsert_dossier_source(
            conn,
            code="7203",
            url="https://example.com/a",
            title="記事A",
            summary="要約A",
            published_at="2026-06-01",
            source_type="news",
        )
        repo.upsert_dossier_source(
            conn,
            code="7203",
            url="https://example.com/b",
            title="記事B",
            summary="要約B",
            published_at="2026-06-03",
            source_type="news",
        )

    body = client.get("/dossiers/7203").json()
    assert body["summary_md"].startswith("# トヨタ")
    assert body["key_facts"] == {"per": 10.5, "topic": "増配"}  # JSON → obj
    assert body["last_investigated_at"] == now
    urls = [s["url"] for s in body["sources"]]
    assert urls == ["https://example.com/b", "https://example.com/a"]  # published_at 降順
    # 本文列は存在しない（要約＋URL のみ＝ADR-020）。
    assert "body" not in body["sources"][0]


def test_post_investigate_returns_latest_dossier(client: Any, monkeypatch: Any) -> None:
    """POST investigate は investigate_stock を同期実行し最新ドシエを返す（L-23・spec §5.2）。

    investigate_stock をモックし、渡された conn にドシエを書く「体」にする
    （実 LLM/fetch_news は呼ばない）。mode="chat" で呼ばれることも検証。
    """
    repo.upsert_stocks([STOCK_A])
    called: dict[str, Any] = {}

    async def fake_investigate(conn: Any, code: str, *, mode: str) -> dict[str, Any]:
        called["code"] = code
        called["mode"] = mode
        ts = "2026-06-05T02:00:00+00:00"
        repo.upsert_dossier(
            conn,
            code=code,
            summary_md="調査済み要約",
            key_facts=json.dumps({"per": 9.9}, ensure_ascii=False),
            last_investigated_at=ts,
            updated_at=ts,
        )
        repo.upsert_dossier_source(
            conn,
            code=code,
            url="https://example.com/new",
            title="新着",
            summary="新着要約",
            published_at="2026-06-04",
            source_type="news",
        )
        return {"code": code, "n_sources_added": 1}

    monkeypatch.setattr("app.routers.dossier.investigate_stock", fake_investigate)

    res = client.post("/dossiers/7203/investigate")
    assert res.status_code == 200
    assert called == {"code": "7203", "mode": "chat"}
    dossier = res.json()["dossier"]
    assert dossier["code"] == "7203"
    assert dossier["summary_md"] == "調査済み要約"
    assert dossier["key_facts"] == {"per": 9.9}
    assert dossier["last_investigated_at"] == "2026-06-05T02:00:00+00:00"
    assert dossier["sources"][0]["url"] == "https://example.com/new"
