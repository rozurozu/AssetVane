"""ニュース3層文脈の下ごしらえ＋組み立て（ADR-044・ADR-014）。

設計の真実: docs/decisions.md ADR-044（ニュースを統合コーパスと階層タグに集約し
get_news_context で3層を必ず揃える）。

1 銘柄を語るには (i) その銘柄自身／(ii) その銘柄のセクター／(iii) マーケット全体、の
**3 階層の文脈**が要る。統合コーパス（news 表・level タグ付き）から 3 層をタグフィルタで
構造的に取り出し、(iii) のマクロ層が意味検索で埋もれる問題を回避する。

AI は受け取った事実を解釈するだけ（ADR-014）。ここは repo（list_news/get_stock）と
LLM の間に立つ軽量オーケストレーションで、数値計算も判定も持たない。本文は持たず
要約＋URL のみ（ADR-020 堅持）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Connection

from app.db import repo
from app.services.lead_lag import JP_SECTOR_LABELS

# 各層の取得窓（日）と件数上限（ADR-044 のタスク指定）。銘柄層は履歴を広めに、
# セクター/市況層は直近のみ。3 層キーは常に揃える（データが無くても空配列）。
_STOCK_SINCE_DAYS = 30
_STOCK_LIMIT = 8
_SECTOR_SINCE_DAYS = 7
_SECTOR_LIMIT = 5
_MARKET_SINCE_DAYS = 7
_MARKET_LIMIT = 6


def _since(days: int) -> str:
    """今日から days 日前の 'YYYY-MM-DD'（list_news の since 下限）。"""
    return (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")


def _article(row: dict[str, Any]) -> dict[str, Any]:
    """news 行（repo の dict）を Tool 返却用の整形済み記事に絞る（本文は持たない・ADR-020）。"""
    return {
        "url": row["url"],
        "title": row.get("title"),
        "summary": row.get("summary"),
        "published_at": row.get("published_at"),
        "source": row.get("source"),
    }


def build_news_context(conn: Connection, code: str) -> dict[str, Any]:
    """銘柄 code の3層ニュース文脈（銘柄/セクター/市況）を必ず揃えて返す（ADR-044・ADR-014）。

    銘柄の sector17_code を get_stock で解決し、TOPIX-17 業種和名（JP_SECTOR_LABELS）で
    sector_label を補う。3 層を list_news のタグフィルタで個別に引く:
      - 銘柄層 = level='stock', code=code（直近 30 日・最大 8 件）
      - セクター層 = level='sector', sector17_code=<解決値>（直近 7 日・最大 5 件・不明なら空）
      - マーケット層 = level='market'（直近 7 日・最大 6 件）

    3 層キー（stock/sector/market）は**常に存在**させる（データが無くても空配列）。未追跡/未上場の
    銘柄は company_name=None・stock 空でも、セクター/市況層は返す。AI は事実を解釈するだけ。
    """
    stock = repo.get_stock(conn, code)
    company_name = (stock or {}).get("company_name")
    sector17_code = (stock or {}).get("sector17_code")
    sector_label = JP_SECTOR_LABELS.get(sector17_code) if sector17_code else None

    stock_rows = repo.list_news(
        conn, level="stock", code=code, since=_since(_STOCK_SINCE_DAYS), limit=_STOCK_LIMIT
    )
    sector_rows = (
        repo.list_news(
            conn,
            level="sector",
            sector17_code=sector17_code,
            since=_since(_SECTOR_SINCE_DAYS),
            limit=_SECTOR_LIMIT,
        )
        if sector17_code
        else []
    )
    market_rows = repo.list_news(
        conn, level="market", since=_since(_MARKET_SINCE_DAYS), limit=_MARKET_LIMIT
    )

    return {
        "code": code,
        "company_name": company_name,
        "sector17_code": sector17_code,
        "sector_label": sector_label,
        "stock": [_article(r) for r in stock_rows],
        "sector": [_article(r) for r in sector_rows],
        "market": [_article(r) for r in market_rows],
    }
