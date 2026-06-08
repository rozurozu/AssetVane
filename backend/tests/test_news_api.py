"""ニュース REST API テスト（GET/POST/DELETE /news・ADR-046/047）。

`client` フィクスチャ（alembic 経路で一時 SQLite）で叩く。要約 LLM は monkeypatch で差し替え
（ネットに出ない＝testing-strategy）。
検証対象:
- GET: 空でも 200・items=[]／level・since・limit フィルタが効く。
- POST: 成功で 200・NewsItem 形／要約失敗（LLM 例外）で 502・その後 GET で 0 件（保存されない）。
- DELETE: source='user' は ok=true／非 user・不在は 404。

POST は services.news.summarize_article を patch して LLM を呼ばない。GET 用のデータは
repo.upsert_news で直接仕込む（POST 経由でも仕込めるが、フィルタ検証は直接投入が速い）。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.db import repo
from app.db.engine import get_engine
from app.services import news as news_service

# 直近30日の既定 since を必ず通す未来日でシードする（router の since は now 基準）。
_FUTURE = "2999-12-31"


def _seed(rows: list[dict]) -> None:
    """統合コーパス news へ直接投入する（ADR-044）。"""
    with get_engine().begin() as conn:
        repo.upsert_news(conn, rows)


def _row(
    url: str,
    *,
    level: str = "market",
    code: str | None = None,
    sector17_code: str | None = None,
    category: str | None = "市況",
    source: str = "news",
    published_at: str | None = _FUTURE,
) -> dict:
    return {
        "level": level,
        "code": code,
        "sector17_code": sector17_code,
        "category": category,
        "source": source,
        "url": url,
        "title": f"{url} のタイトル",
        "summary": "要約。",
        "published_at": published_at,
        "fetched_at": None,
        "extraction_status": "summarized",
    }


async def _fake_summary(text: str) -> str:
    """要約 LLM の差し替え（決定的・ネットに出ない）。"""
    return "要約済み"


# ---------------------------------------------------------------------------
# GET /news
# ---------------------------------------------------------------------------


def test_get_news_empty_returns_200(client: Any) -> None:
    """台帳が空でも 200・items=[]。"""
    res = client.get("/news")
    assert res.status_code == 200
    assert res.json() == {"items": []}


def test_get_news_returns_items(client: Any) -> None:
    """投入後は NewsItem の list を返す。"""
    _seed([_row("https://m/1")])
    res = client.get("/news")
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) == 1
    assert items[0]["url"] == "https://m/1"
    assert items[0]["level"] == "market"
    assert items[0]["source"] == "news"
    assert "id" in items[0]


def test_get_news_level_filter(client: Any) -> None:
    """level フィルタで該当層だけ返る。"""
    with get_engine().begin() as conn:
        conn.exec_driver_sql("INSERT INTO stocks (code, company_name) VALUES ('7203', 'トヨタ')")
    _seed(
        [
            _row("https://m/1", level="market"),
            _row("https://s/1", level="stock", code="7203", category=None),
        ]
    )
    res = client.get("/news", params={"level": "stock"})
    assert res.status_code == 200
    items = res.json()["items"]
    assert [i["url"] for i in items] == ["https://s/1"]


def test_get_news_since_filter(client: Any) -> None:
    """since 指定で published_at >= since に絞る。"""
    _seed(
        [
            _row("https://m/old", published_at="2026-06-01"),
            _row("https://m/new", published_at=_FUTURE),
        ]
    )
    res = client.get("/news", params={"since": "2026-06-04"})
    assert res.status_code == 200
    items = res.json()["items"]
    assert [i["url"] for i in items] == ["https://m/new"]


def test_get_news_limit(client: Any) -> None:
    """limit で件数を絞る。"""
    _seed(
        [
            _row("https://m/1", published_at="2999-12-01"),
            _row("https://m/2", published_at="2999-12-02"),
            _row("https://m/3", published_at="2999-12-03"),
        ]
    )
    res = client.get("/news", params={"limit": 2})
    assert res.status_code == 200
    assert len(res.json()["items"]) == 2


# ---------------------------------------------------------------------------
# POST /news
# ---------------------------------------------------------------------------


def test_post_news_success(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """貼付テキストを要約して取り込み、NewsItem を 200 で返す（ADR-046）。"""
    monkeypatch.setattr(news_service, "summarize_article", _fake_summary)
    res = client.post("/news", json={"text": "貼り付けた本文。"})
    assert res.status_code == 200
    body = res.json()
    assert body["level"] == "market"
    assert body["source"] == "user"
    assert body["category"] == "ユーザー投入"
    assert body["summary"] == "要約済み"
    assert body["url"].startswith("user://")
    assert "id" in body

    # 取り込み後は GET で読める。
    listed = client.get("/news").json()["items"]
    assert any(i["url"] == body["url"] for i in listed)


def test_post_news_summary_failure_returns_502(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """要約失敗（LLM 例外）は 502・その後 GET で 0 件（保存されない・ADR-046）。"""

    async def _boom(text: str) -> str:
        raise RuntimeError("LLM 障害")

    monkeypatch.setattr(news_service, "summarize_article", _boom)
    res = client.post("/news", json={"text": "本文。"})
    assert res.status_code == 502
    # 失敗時は何も保存されない。
    assert client.get("/news").json()["items"] == []


# ---------------------------------------------------------------------------
# DELETE /news/{id}
# ---------------------------------------------------------------------------


def test_delete_news_user_ok(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """ユーザー投入（source='user'）は削除でき ok=true（ADR-046）。"""
    monkeypatch.setattr(news_service, "summarize_article", _fake_summary)
    created = client.post("/news", json={"text": "消す本文。"}).json()
    res = client.delete(f"/news/{created['id']}")
    assert res.status_code == 200
    assert res.json() == {"ok": True}
    # 実際に消えている。
    assert client.get("/news").json()["items"] == []


def test_delete_news_non_user_returns_404(client: Any) -> None:
    """source='user' でない行（自動取得）は id 一致でも 404（消さない・ADR-046）。"""
    _seed([_row("https://m/keep", source="news")])
    with get_engine().connect() as conn:
        row = repo.get_news_by_url(conn, "https://m/keep")
    assert row is not None
    res = client.delete(f"/news/{row['id']}")
    assert res.status_code == 404
    # 残っている。
    assert len(client.get("/news").json()["items"]) == 1


def test_delete_news_missing_returns_404(client: Any) -> None:
    """不在 id は 404。"""
    res = client.delete("/news/999999")
    assert res.status_code == 404
