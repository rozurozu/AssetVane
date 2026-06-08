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

from app.db import repo
from app.db.engine import get_engine
from app.db.schema import stocks
from app.services.news import build_news_context

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
