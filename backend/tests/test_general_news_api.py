"""一般ニュース REST API テスト（GET /general-news・ADR-034）。

`client` フィクスチャ（alembic 経路で一時 SQLite＝0011 が当たる）で叩く。
検証対象:
- 台帳が空でも 200 で categories=[]（widget が壊れない）。
- 投入後はカテゴリ別にグルーピングして返す。
発行日は lookback（既定 2 日）に左右されないよう未来日でシードする（router の since は now 基準）。
"""

from __future__ import annotations

from typing import Any

from app.db import repo
from app.db.engine import get_engine

# lookback（直近 N 日）を必ず通す未来日でシードする。
_FUTURE = "2999-12-31"


def _seed(rows: list[dict]) -> None:
    with get_engine().begin() as conn:
        repo.upsert_general_news(conn, rows)


def test_general_news_empty_returns_200(client: Any) -> None:
    """台帳が空でも 200・categories=[]。"""
    res = client.get("/general-news")
    assert res.status_code == 200
    assert res.json() == {"categories": []}


def test_general_news_grouped_by_category(client: Any) -> None:
    """投入後はカテゴリ別にまとまって返る（label と items の対応）。"""
    _seed(
        [
            {
                "category": "市況",
                "url": "https://m.example/1",
                "title": "日経上昇",
                "summary": "要約1",
                "published_at": _FUTURE,
                "source_type": "news",
                "extraction_status": "summarized",
            },
            {
                "category": "市況",
                "url": "https://m.example/2",
                "title": "東証続伸",
                "summary": "要約2",
                "published_at": _FUTURE,
                "source_type": "news",
                "extraction_status": "summarized",
            },
            {
                "category": "マクロ",
                "url": "https://k.example/1",
                "title": "日銀会合",
                "summary": "要約3",
                "published_at": _FUTURE,
                "source_type": "news",
                "extraction_status": "summarized",
            },
        ]
    )
    res = client.get("/general-news")
    assert res.status_code == 200
    cats = {c["label"]: c["items"] for c in res.json()["categories"]}
    assert set(cats) == {"市況", "マクロ"}
    assert len(cats["市況"]) == 2
    assert len(cats["マクロ"]) == 1
    item = cats["マクロ"][0]
    assert item["url"] == "https://k.example/1"
    assert item["category"] == "マクロ"
    assert item["title"] == "日銀会合"
