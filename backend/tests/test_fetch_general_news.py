"""fetch_general_news（一般ニュース・ADR-034）の単体テスト（ネット非依存）。

testing-strategy: 外部 API は叩かず、NewsAdapter の内部関数（_fetch_rss_items / _decode_google_url
/ _fetch_html / _summarize_article）と config 定数（カテゴリ・lookback・件数上限）を monkeypatch
する。担保すること（ADR-034 確定事項）:
  - 各記事に正しい category（ラベル）が付く・カテゴリ数ぶんループする
  - lookback（since）フィルタ・カテゴリあたり件数キャップ
  - 1 カテゴリの RSS 取得失敗（NewsAdapterError）でも他カテゴリは継続（ADR-018）
  - news_enabled=False で空配列
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.adapters import general_news_config, news
from app.config import settings


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _enable_news(monkeypatch: pytest.MonkeyPatch) -> None:
    """news を有効化し throttle を無効化。カテゴリ・件数・lookback を既知値に固定する。"""
    monkeypatch.setattr(settings, "news_enabled", True)
    monkeypatch.setattr(settings, "news_min_interval_seconds", 0.0)

    async def _no_throttle() -> None:
        return None

    monkeypatch.setattr(news, "_throttle", _no_throttle)
    monkeypatch.setattr(
        general_news_config,
        "GENERAL_NEWS_CATEGORIES",
        [
            {"label": "市況", "query": "市況クエリ"},
            {"label": "マクロ", "query": "マクロクエリ"},
        ],
    )
    monkeypatch.setattr(general_news_config, "GENERAL_NEWS_MAX_PER_CATEGORY", 5)
    # 充分長い lookback（日付フィルタに引っかからない＝デフォは category/cap 検証に集中）。
    monkeypatch.setattr(general_news_config, "GENERAL_NEWS_LOOKBACK_DAYS", 36500)


def _patch_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    *,
    items_by_query: dict[str, list[dict]] | None = None,
    fail_queries: set[str] | None = None,
) -> None:
    """_fetch_rss_items（クエリ別）・URL 復元・本文取得・要約を monkeypatch する。"""
    fail_queries = fail_queries or set()

    async def _items(query: str) -> list[dict]:
        if query in fail_queries:
            raise news.NewsAdapterError(f"RSS 失敗（{query}）")
        if items_by_query is not None:
            return list(items_by_query.get(query, []))
        # 既定: クエリごとに 1 件（published_at=None で since を常に通す）。
        return [
            {
                "title": f"{query} の記事",
                "link": "https://news.google.com/rss/articles/X?oc=5",
                "published_at": None,
                "source": None,
            }
        ]

    async def _decode(url: str) -> str:
        # 記事ごとに一意な実 URL（url UNIQUE を壊さない）。link のクエリ部を流用。
        return f"https://media.example.com/{abs(hash(url)) % 100000}"

    async def _html(url: str) -> str:
        # trafilatura が本文として抽出できる十分な長さの HTML（test_news_adapter と同流儀）。
        return (
            "<html><head><title>記事</title></head><body><article><p>"
            + ("市況は堅調に推移し、為替と金利の動向が注目された。" * 12)
            + "</p></article></body></html>"
        )

    async def _summarize(text: str) -> str:
        return "要約された 2 行。"

    monkeypatch.setattr(news, "_fetch_rss_items", _items)
    monkeypatch.setattr(news, "_decode_google_url", _decode)
    monkeypatch.setattr(news, "_fetch_html", _html)
    monkeypatch.setattr(news, "_summarize_article", _summarize)


def test_disabled_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """news_enabled=False なら即 []。"""
    monkeypatch.setattr(settings, "news_enabled", False)
    assert _run(news.fetch_general_news()) == []


def test_category_assigned_and_looped(monkeypatch: pytest.MonkeyPatch) -> None:
    """各記事に category が付き、カテゴリ数ぶんループする（2 カテゴリ × 1 件＝2 件）。"""
    _patch_pipeline(monkeypatch)
    result = _run(news.fetch_general_news())
    assert len(result) == 2
    assert {a["category"] for a in result} == {"市況", "マクロ"}
    assert all(a["source_type"] == "news" for a in result)
    assert all(a["extraction_status"] == "summarized" for a in result)


def test_per_category_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """カテゴリあたり GENERAL_NEWS_MAX_PER_CATEGORY 件で打ち切る。"""
    monkeypatch.setattr(general_news_config, "GENERAL_NEWS_MAX_PER_CATEGORY", 1)
    three = [
        {
            "title": f"記事{i}",
            "link": f"https://news.google.com/rss/articles/{i}?oc=5",
            "published_at": None,
            "source": None,
        }
        for i in range(3)
    ]
    _patch_pipeline(monkeypatch, items_by_query={"市況クエリ": three, "マクロクエリ": three})
    result = _run(news.fetch_general_news())
    # 2 カテゴリ × 上限 1 件 = 2 件。
    assert len(result) == 2


def test_lookback_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    """lookback より古い発行日は弾く（published_at < since は除外）。"""
    monkeypatch.setattr(general_news_config, "GENERAL_NEWS_LOOKBACK_DAYS", 2)
    # 未来日（必ず since 以上）と大昔（必ず since 未満）を 1 件ずつ。
    items = [
        {
            "title": "新しい",
            "link": "https://news.google.com/rss/articles/NEW?oc=5",
            "published_at": "2999-12-31",
            "source": None,
        },
        {
            "title": "古い",
            "link": "https://news.google.com/rss/articles/OLD?oc=5",
            "published_at": "2000-01-01",
            "source": None,
        },
    ]
    _patch_pipeline(monkeypatch, items_by_query={"市況クエリ": items, "マクロクエリ": []})
    result = _run(news.fetch_general_news())
    assert len(result) == 1
    assert result[0]["title"] == "新しい"


def test_one_category_failure_continues(monkeypatch: pytest.MonkeyPatch) -> None:
    """1 カテゴリの RSS 取得失敗でも他カテゴリは継続する（ADR-018）。"""
    _patch_pipeline(monkeypatch, fail_queries={"市況クエリ"})
    result = _run(news.fetch_general_news())
    # 市況は失敗で 0 件、マクロのみ 1 件。
    assert len(result) == 1
    assert result[0]["category"] == "マクロ"
