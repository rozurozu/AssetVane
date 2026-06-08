"""fetch_sector_news（セクターニュース・ADR-044 (ii) セクター層）の単体テスト（ネット非依存）。

testing-strategy: 外部 API は叩かず、NewsAdapter の内部関数（_fetch_rss_items / _decode_google_url
/ _fetch_html / summarize_article）と config 定数（業種クエリ・lookback・件数上限）を monkeypatch
する。担保すること（ADR-044・ADR-053 確定事項）:
  - 各記事に統合タグ（level='sector'・sector17_code・category・source='news'）が付く
  - sector17_code は J-Quants S17 体系 "1".."17"（ADR-053。stocks と直接一致）。category は
    reference.sector17_label(s17) 由来の和名。
  - SECTOR_NEWS_QUERIES は `dict[str, str]`（キー=S17・値=query のみ・label 廃止・ADR-053）。
  - 業種数ぶんループする
  - lookback（since）フィルタ・業種あたり件数キャップ
  - known_urls に含む url は要約スキップ＋出力除外（ADR-044 要約前 dedup の逆輸入）
  - 1 業種の RSS 取得失敗（NewsAdapterError）でも他業種は継続（ADR-018）
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
    """news を有効化し throttle を無効化。業種クエリ・件数・lookback を既知値に固定する。"""
    monkeypatch.setattr(settings, "news_enabled", True)
    monkeypatch.setattr(settings, "news_min_interval_seconds", 0.0)

    async def _no_throttle() -> None:
        return None

    monkeypatch.setattr(news, "_throttle", _no_throttle)
    # 2 業種だけに絞って検証（ADR-053: キー=S17 "1".."17"・値=query 文字列のみ。
    # 和名は adapter 側が reference.sector17_label(s17) で引く＝"1"=食品 / "6"=自動車・輸送機）。
    monkeypatch.setattr(
        general_news_config,
        "SECTOR_NEWS_QUERIES",
        {
            "1": "食品クエリ",
            "6": "自動車クエリ",
        },
    )
    monkeypatch.setattr(general_news_config, "SECTOR_NEWS_MAX_PER_SECTOR", 3)
    # 充分長い lookback（日付フィルタに引っかからない＝tag/cap 検証に集中）。
    monkeypatch.setattr(general_news_config, "SECTOR_NEWS_LOOKBACK_DAYS", 36500)


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
        return [
            {
                "title": f"{query} の記事",
                "link": "https://news.google.com/rss/articles/X?oc=5",
                "published_at": None,
                "source": None,
            }
        ]

    async def _decode(url: str) -> str:
        # 記事ごとに一意な実 URL（url UNIQUE を壊さない）。link を流用。
        return f"https://media.example.com/{abs(hash(url)) % 100000}"

    async def _html(url: str) -> str:
        return (
            "<html><head><title>記事</title></head><body><article><p>"
            + ("業界の動向が注目され、需給と業績の見通しが話題になった。" * 12)
            + "</p></article></body></html>"
        )

    async def _summarize(text: str) -> str:
        return "要約された 2 行。"

    monkeypatch.setattr(news, "_fetch_rss_items", _items)
    monkeypatch.setattr(news, "_decode_google_url", _decode)
    monkeypatch.setattr(news, "_fetch_html", _html)
    monkeypatch.setattr(news, "summarize_article", _summarize)


def test_disabled_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """news_enabled=False なら即 []。"""
    monkeypatch.setattr(settings, "news_enabled", False)
    assert _run(news.fetch_sector_news()) == []


def test_sector_tags_assigned_and_looped(monkeypatch: pytest.MonkeyPatch) -> None:
    """各記事に level='sector'・sector17_code(S17)・category(和名)・source が付きループする。

    sector17_code は S17 体系 "1".."17"（ADR-053）。category は reference.sector17_label(s17)
    由来の和名（"1"→食品 / "6"→自動車・輸送機）。adapter は config の label を写さない。
    """
    _patch_pipeline(monkeypatch)
    result = _run(news.fetch_sector_news())
    assert len(result) == 2
    assert all(a["level"] == "sector" for a in result)
    assert all(a["source"] == "news" for a in result)
    assert {a["sector17_code"] for a in result} == {"1", "6"}
    assert {a["category"] for a in result} == {"食品", "自動車・輸送機"}
    # sector17_code(S17) と category（reference 由来の和名）が同じ記事内で対応している。
    by_code = {a["sector17_code"]: a["category"] for a in result}
    assert by_code["1"] == "食品"
    assert by_code["6"] == "自動車・輸送機"


def test_per_sector_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """業種あたり SECTOR_NEWS_MAX_PER_SECTOR 件で打ち切る。"""
    monkeypatch.setattr(general_news_config, "SECTOR_NEWS_MAX_PER_SECTOR", 1)
    five = [
        {
            "title": f"記事{i}",
            "link": f"https://news.google.com/rss/articles/{i}?oc=5",
            "published_at": None,
            "source": None,
        }
        for i in range(5)
    ]
    _patch_pipeline(monkeypatch, items_by_query={"食品クエリ": five, "自動車クエリ": five})
    result = _run(news.fetch_sector_news())
    # 2 業種 × 上限 1 件 = 2 件。
    assert len(result) == 2


def test_lookback_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    """lookback より古い発行日は弾く（published_at < since は除外）。"""
    monkeypatch.setattr(general_news_config, "SECTOR_NEWS_LOOKBACK_DAYS", 3)
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
    _patch_pipeline(monkeypatch, items_by_query={"食品クエリ": items, "自動車クエリ": []})
    result = _run(news.fetch_sector_news())
    assert len(result) == 1
    assert result[0]["title"] == "新しい"


def test_known_urls_skipped_and_excluded(monkeypatch: pytest.MonkeyPatch) -> None:
    """known_urls に含む url（Google link）は要約スキップ＋出力から除外（ADR-044 dedup）。"""
    summarize_calls: list[str] = []

    items = {
        "食品クエリ": [
            {
                "title": "既存",
                "link": "https://news.google.com/rss/articles/KNOWN?oc=5",
                "published_at": None,
                "source": None,
            },
            {
                "title": "新規",
                "link": "https://news.google.com/rss/articles/FRESH?oc=5",
                "published_at": None,
                "source": None,
            },
        ],
        "自動車クエリ": [],
    }
    _patch_pipeline(monkeypatch, items_by_query=items)

    async def _summarize_counting(text: str) -> str:
        summarize_calls.append(text)
        return "要約された 2 行。"

    monkeypatch.setattr(news, "summarize_article", _summarize_counting)

    known = {"https://news.google.com/rss/articles/KNOWN?oc=5"}
    result = _run(news.fetch_sector_news(known))

    # 既存 1 件は除外され、新規 1 件のみ。要約も新規 1 件ぶんだけ走る。
    assert len(result) == 1
    assert result[0]["title"] == "新規"
    assert len(summarize_calls) == 1


def test_one_sector_failure_continues(monkeypatch: pytest.MonkeyPatch) -> None:
    """1 業種の RSS 取得失敗でも他業種は継続する（ADR-018）。"""
    _patch_pipeline(monkeypatch, fail_queries={"食品クエリ"})
    result = _run(news.fetch_sector_news())
    # 食品は失敗で 0 件、自動車のみ 1 件（S17 "6"）。
    assert len(result) == 1
    assert result[0]["sector17_code"] == "6"
