"""services.news.build_news_context（ニュース3層文脈）の検証（ADR-044・ADR-053・ADR-014）。

担保すること:
- 3 層キー（stock/sector/market）が**常に存在**する（銘柄層が空でもキーは出る）。
- sector_label が S17 業種コード（reference.sector17_label）で和名解決される（ADR-053）。
  銘柄の sector17_code・news.sector17_code は同じ J-Quants S17 体系 "1".."17"（直接一致）。
- 未追跡銘柄でも company_name=None・stock 空で、セクター/市況層は返る。
- "99"（ETF/REIT）・None の銘柄はセクター層が空になる（normalize_sector17 で None・ADR-053）。
- 記事は本文を持たず url/title/summary/published_at/source の整形済み形（ADR-020）。

本物の DB に触れず一時 SQLite で回す。ネットには出ない（repo.upsert_news で行を仕込んでから
検証する＝testing-strategy）。since は今日基準の相対窓なので、published_at は今日の日付を使う。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.db import repo
from app.db.engine import get_engine
from app.db.schema import stocks
from app.services import news as news_service
from app.services.news import (
    _resolve_user_tags,
    _user_news_url,
    build_news_context,
    ingest_user_news,
)

_TODAY = datetime.now(UTC).strftime("%Y-%m-%d")


def _row(
    url: str,
    *,
    level: str,
    code: str | None = None,
    sector17_code: str | None = None,
    category: str | None = None,
    source: str = "news",
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
        "published_at": _TODAY,
        "fetched_at": None,
        "extraction_status": "summarized",
    }


def _seed_stock(code: str, *, sector17_code: str | None) -> None:
    """stock 層 news は code FK を持つので銘柄を先に入れる（sector17_code でセクター解決）。"""
    with get_engine().begin() as conn:
        conn.execute(
            stocks.insert().values(
                code=code,
                company_name=f"{code} 社",
                sector17_code=sector17_code,
            )
        )


def test_build_news_context_three_layers_always_present(temp_db) -> None:
    """3 層キー（stock/sector/market）は銘柄層が空でも必ず存在する（ADR-044）。"""
    # 市況層のみ仕込む（銘柄/セクター層は無し）。code 未登録銘柄を引く。
    with get_engine().begin() as conn:
        repo.upsert_news(conn, [_row("https://m/1", level="market", category="市況")])

    with get_engine().connect() as conn:
        ctx = build_news_context(conn, "7203")

    # 3 層キーは常に存在（無くても空配列）。
    assert ctx["stock"] == []
    assert ctx["sector"] == []
    assert len(ctx["market"]) == 1
    # 未登録銘柄は company_name=None でも他層は返る。
    assert ctx["code"] == "7203"
    assert ctx["company_name"] is None
    assert ctx["sector17_code"] is None
    assert ctx["sector_label"] is None


def test_build_news_context_resolves_sector_label_and_layers(temp_db) -> None:
    """銘柄の sector17_code(S17) を解決し和名化・3 層を揃える（ADR-044・ADR-053）。

    実 J-Quants 値で通す: トヨタ(7203) の sector17_code は S17 "6"（自動車・輸送機）。
    news.sector17_code も同じ S17 "6" でタグ付けされ、変換なしで直接一致する（ADR-053）。
    """
    _seed_stock("7203", sector17_code="6")  # 自動車・輸送機（S17）
    with get_engine().begin() as conn:
        repo.upsert_news(conn, [_row("https://s/1", level="stock", code="7203", source="news")])
        repo.upsert_news(conn, [_row("https://sec/1", level="sector", sector17_code="6")])
        repo.upsert_news(conn, [_row("https://m/1", level="market", category="市況")])

    with get_engine().connect() as conn:
        ctx = build_news_context(conn, "7203")

    assert ctx["company_name"] == "7203 社"
    assert ctx["sector17_code"] == "6"
    assert ctx["sector_label"] == "自動車・輸送機"
    assert len(ctx["stock"]) == 1
    assert len(ctx["sector"]) == 1
    assert len(ctx["market"]) == 1
    # 記事は本文を持たず整形済みキーのみ（ADR-020）。
    assert set(ctx["stock"][0]) == {"url", "title", "summary", "published_at", "source"}


def test_build_news_context_unknown_sector_skips_sector_layer(temp_db) -> None:
    """sector17_code 不明（銘柄に業種なし＝None）ならセクター層は空・他層は返る（ADR-044）。"""
    _seed_stock("9999", sector17_code=None)
    with get_engine().begin() as conn:
        repo.upsert_news(conn, [_row("https://m/1", level="market", category="市況")])
        # セクター層の記事はあるが、銘柄に業種が無いので引かれない。
        repo.upsert_news(conn, [_row("https://sec/1", level="sector", sector17_code="6")])

    with get_engine().connect() as conn:
        ctx = build_news_context(conn, "9999")

    assert ctx["sector17_code"] is None
    assert ctx["sector_label"] is None
    assert ctx["sector"] == []  # 業種未解決ならセクター層は引かない
    assert ctx["stock"] == []  # 銘柄層ニュース未投入
    assert len(ctx["market"]) == 1


def test_build_news_context_etf_sector_99_skips_sector_layer(temp_db) -> None:
    """sector17_code="99"（ETF/REIT）は normalize で None になり sector 層が空（ADR-053）。"""
    _seed_stock("1306", sector17_code="99")  # ETF（S17 では "99" 扱い＝分類なし）
    with get_engine().begin() as conn:
        repo.upsert_news(conn, [_row("https://m/1", level="market", category="市況")])
        # S17 "6" のセクター記事はあるが、ETF の "99" は業種未解決なので引かれない。
        repo.upsert_news(conn, [_row("https://sec/1", level="sector", sector17_code="6")])

    with get_engine().connect() as conn:
        ctx = build_news_context(conn, "1306")

    assert ctx["sector17_code"] is None  # "99" → None（ADR-053）
    assert ctx["sector_label"] is None
    assert ctx["sector"] == []
    assert len(ctx["market"]) == 1


def test_build_news_context_untracked_stock_empty_stock_layer(temp_db) -> None:
    """未追跡（stocks 未登録）銘柄は stock 層が空でも market 層は返る（ADR-044）。"""
    with get_engine().begin() as conn:
        repo.upsert_news(conn, [_row("https://m/1", level="market", category="市況")])

    with get_engine().connect() as conn:
        ctx = build_news_context(conn, "0000")

    assert ctx["company_name"] is None
    assert ctx["stock"] == []
    assert len(ctx["market"]) == 1


# ===========================================================================
# ADR-046: ingest_user_news（ユーザー貼付テキスト → 要約 → news 取り込み）
# ===========================================================================


import asyncio  # noqa: E402 — 以降のテスト専用（既存セクションの import を汚さない）


def _run(coro):
    """async 関数を 1 回駆動するヘルパ（テスト専用・新しいイベントループで回す）。"""
    return asyncio.run(coro)


async def _fake_summary(text: str) -> str:
    """要約 LLM の差し替え（ネットに出ない・決定的な戻り値）。"""
    return "要約済み"


def test_resolve_user_tags_pure(temp_db) -> None:
    """_resolve_user_tags は code 有→stock 層・無→market 層、source は常に 'user'（ADR-046）。"""
    assert _resolve_user_tags("7203") == {
        "level": "stock",
        "code": "7203",
        "sector17_code": None,
        "source": "user",
    }
    assert _resolve_user_tags(None) == {
        "level": "market",
        "code": None,
        "sector17_code": None,
        "source": "user",
    }


def test_user_news_url_pure() -> None:
    """_user_news_url は url 優先・無ければ本文ハッシュの合成キー（user://+16桁・ADR-046）。"""
    # url ありはそのまま（前後空白は除去）。
    assert _user_news_url("  https://e/1  ", "本文") == "https://e/1"
    # url 無しは本文ハッシュ。同じ本文は同じキー（冪等）、違う本文は違うキー。
    u1 = _user_news_url(None, "同じ本文")
    u2 = _user_news_url("", "同じ本文")
    u3 = _user_news_url(None, "別の本文")
    assert u1 == u2  # None も空も合成キー、同一本文で一致
    assert u1.startswith("user://")
    assert len(u1) == len("user://") + 16  # 16 桁 hex
    assert u1 != u3


def test_ingest_user_news_market_layer(temp_db, monkeypatch) -> None:
    """code 未指定は market 層・category='ユーザー投入'・source='user'・要約が入る（ADR-046）。"""
    monkeypatch.setattr(news_service, "summarize_article", _fake_summary)
    saved = _run(ingest_user_news(text="貼り付けた本文。"))
    assert saved["level"] == "market"
    assert saved["code"] is None
    assert saved["category"] == "ユーザー投入"
    assert saved["source"] == "user"
    assert saved["summary"] == "要約済み"
    assert saved["url"].startswith("user://")
    assert saved["published_at"] == _TODAY
    assert "id" in saved  # get_news_by_url の確定行を返す

    with get_engine().connect() as conn:
        rows = repo.list_news(conn, level="market")
    assert len(rows) == 1


def test_ingest_user_news_stock_layer_with_code(temp_db, monkeypatch) -> None:
    """code 指定は stock 層・category None（ADR-046）。"""
    monkeypatch.setattr(news_service, "summarize_article", _fake_summary)
    _seed_stock("7203", sector17_code="6")  # news.code は stocks.code への FK（先に銘柄を入れる）
    saved = _run(ingest_user_news(text="トヨタの本文。", code="7203"))
    assert saved["level"] == "stock"
    assert saved["code"] == "7203"
    assert saved["category"] is None
    assert saved["source"] == "user"


def test_ingest_user_news_uses_given_url(temp_db, monkeypatch) -> None:
    """url 入力はそのまま保存キーに使う（合成キーにしない・ADR-046）。"""
    monkeypatch.setattr(news_service, "summarize_article", _fake_summary)
    saved = _run(ingest_user_news(text="本文。", url="https://media/article/1"))
    assert saved["url"] == "https://media/article/1"


def test_ingest_user_news_idempotent_same_text(temp_db, monkeypatch) -> None:
    """url 無しで同じ本文を 2 回投入しても 1 行に収束する（本文ハッシュ＋UPSERT・ADR-046）。"""
    monkeypatch.setattr(news_service, "summarize_article", _fake_summary)
    _run(ingest_user_news(text="同一本文。"))
    _run(ingest_user_news(text="同一本文。"))
    with get_engine().connect() as conn:
        rows = repo.list_news(conn)
    assert len(rows) == 1


def test_ingest_user_news_propagates_summary_failure(temp_db, monkeypatch) -> None:
    """要約失敗は握らず伝播する（router が 502 に翻訳・ADR-046）。"""

    async def _boom(text: str) -> str:
        raise RuntimeError("LLM 障害")

    monkeypatch.setattr(news_service, "summarize_article", _boom)
    with pytest.raises(RuntimeError):
        _run(ingest_user_news(text="本文。"))
    # 失敗時は何も保存しない。
    with get_engine().connect() as conn:
        assert repo.list_news(conn) == []
