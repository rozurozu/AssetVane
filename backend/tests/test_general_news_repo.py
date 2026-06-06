"""一般ニュース台帳の repo（upsert_general_news / list_general_news）を検証する（ADR-034）。

担保すること:
- upsert_general_news の冪等性（同じ url を 2 回入れても 1 行・既存は skip）。
- fetched_at 未指定行は UTC now が補完される。
- list_general_news の発行日降順・since フィルタ。
本物の DB に触れず一時 SQLite で回す（code FK は持たないので stock 投入は不要・testing-strategy）。
"""

from __future__ import annotations

from app.db import repo
from app.db.engine import get_engine


def _row(url: str, *, category: str = "市況", published_at: str | None = "2026-06-05") -> dict:
    return {
        "category": category,
        "url": url,
        "title": f"{url} のタイトル",
        "summary": "要約。",
        "published_at": published_at,
        "source_type": "news",
        "extraction_status": "summarized",
    }


def test_upsert_general_news_dedup_by_url(temp_db) -> None:
    """同じ url を 2 回入れても 1 行（on_conflict_do_nothing・冪等）。"""
    with get_engine().begin() as conn:
        repo.upsert_general_news(conn, [_row("https://a.example/1")])
        repo.upsert_general_news(conn, [_row("https://a.example/1", category="マクロ")])
    with get_engine().connect() as conn:
        rows = repo.list_general_news(conn)
    assert len(rows) == 1
    # 先勝ち（既存 skip）なので最初の category が残る。
    assert rows[0]["category"] == "市況"


def test_upsert_general_news_fills_fetched_at(temp_db) -> None:
    """fetched_at 未指定なら UTC now が補完される。"""
    with get_engine().begin() as conn:
        repo.upsert_general_news(conn, [_row("https://a.example/2")])
    with get_engine().connect() as conn:
        rows = repo.list_general_news(conn)
    assert rows[0]["fetched_at"]  # 非 None・非空


def test_upsert_general_news_empty_returns_zero(temp_db) -> None:
    """空 rows は 0 を返し何も入れない。"""
    with get_engine().begin() as conn:
        assert repo.upsert_general_news(conn, []) == 0
    with get_engine().connect() as conn:
        assert repo.list_general_news(conn) == []


def test_list_general_news_ordered_desc(temp_db) -> None:
    """published_at 降順で返る。"""
    with get_engine().begin() as conn:
        repo.upsert_general_news(
            conn,
            [
                _row("https://a.example/old", published_at="2026-06-01"),
                _row("https://a.example/new", published_at="2026-06-05"),
            ],
        )
    with get_engine().connect() as conn:
        rows = repo.list_general_news(conn)
    assert [r["published_at"] for r in rows] == ["2026-06-05", "2026-06-01"]


def test_list_general_news_since_filter(temp_db) -> None:
    """since 指定で published_at >= since に絞る。"""
    with get_engine().begin() as conn:
        repo.upsert_general_news(
            conn,
            [
                _row("https://a.example/old", published_at="2026-06-01"),
                _row("https://a.example/new", published_at="2026-06-05"),
            ],
        )
    with get_engine().connect() as conn:
        rows = repo.list_general_news(conn, since="2026-06-04")
    assert len(rows) == 1
    assert rows[0]["published_at"] == "2026-06-05"
