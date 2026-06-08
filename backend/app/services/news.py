"""ニュース3層文脈の下ごしらえ＋組み立て（ADR-044・ADR-014・ADR-053）。

設計の真実: docs/decisions.md ADR-044（ニュースを統合コーパスと階層タグに集約し
get_news_context で3層を必ず揃える）。

1 銘柄を語るには (i) その銘柄自身／(ii) その銘柄のセクター／(iii) マーケット全体、の
**3 階層の文脈**が要る。統合コーパス（news 表・level タグ付き）から 3 層をタグフィルタで
構造的に取り出し、(iii) のマクロ層が意味検索で埋もれる問題を回避する。

セクター層は stocks.sector17_code（J-Quants S17 業種コード "1".."17"・ETF/REIT は "99"）で
news.sector17_code（同体系・ADR-053）と直接一致させる。和名ラベルは reference に集約した
SSOT（app.reference.sector_codes）から引く。

AI は受け取った事実を解釈するだけ（ADR-014）。ここは repo（list_news/get_stock）と
LLM の間に立つ軽量オーケストレーションで、数値計算も判定も持たない。本文は持たず
要約＋URL のみ（ADR-020 堅持）。
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Connection

from app.adapters.news import summarize_article
from app.db import repo
from app.db.engine import get_engine
from app.reference.sector_codes import normalize_sector17, sector17_label

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
    """銘柄 code の3層ニュース文脈（銘柄/セクター/市況）を必ず揃えて返す（ADR-044/014/053）。

    銘柄の sector17_code を get_stock で解決し、S17 業種和名（reference.sector17_label）で
    sector_label を補う。sector17_code は J-Quants S17 体系 "1".."17"（ETF/REIT は "99"）で、
    normalize_sector17 で正規化（"99"/None/不正→None でセクター層を空にする・ADR-053）。3 層を
    list_news のタグフィルタで個別に引く:
      - 銘柄層 = level='stock', code=code（直近 30 日・最大 8 件）
      - セクター層 = level='sector', sector17_code=<正規化値>（直近 7 日・最大 5 件・不明なら空）。
        news.sector17_code も同じ S17 体系なので変換なしで直接一致する（ADR-053）。
      - マーケット層 = level='market'（直近 7 日・最大 6 件）

    3 層キー（stock/sector/market）は**常に存在**させる（データが無くても空配列）。未追跡/未上場の
    銘柄は company_name=None・stock 空でも、セクター/市況層は返す。AI は事実を解釈するだけ。
    """
    stock = repo.get_stock(conn, code)
    company_name = (stock or {}).get("company_name")
    sector17_code = normalize_sector17((stock or {}).get("sector17_code"))
    sector_label = sector17_label(sector17_code)

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


# ---------------------------------------------------------------------------
# ADR-046: ユーザー投入ニュース（貼付テキスト → 要約 → 統合コーパス news へ）
# ---------------------------------------------------------------------------

# ユーザー投入で URL が無い記事の合成キー接頭辞（user://<text のハッシュ>）。
_USER_URL_PREFIX = "user://"


def _resolve_user_tags(code: str | None) -> dict[str, Any]:
    """ユーザー投入記事の階層タグ（level/code/sector17_code/source）を決める（ADR-046）。

    v1 はユーザーが明示した分類のみ採用する。code 指定あり → 銘柄層（level='stock'・その code）、
    指定なし → 市況層（level='market'）。source は常に 'user'（自動取得分と区別・削除の安全弁）。

    v2（将来）: code 未指定時に貼付本文から銘柄/セクターを LLM 推定し、ここでタグを補う差し込み口に
    する想定（推定の所在をこの関数に閉じ込め、ingest 本体は呼ぶだけで済むようにしておく）。
    """
    if code:
        return {"level": "stock", "code": code, "sector17_code": None, "source": "user"}
    return {"level": "market", "code": None, "sector17_code": None, "source": "user"}


def _user_news_url(url: str | None, text: str) -> str:
    """ユーザー投入記事の保存 url を決める（実 URL 優先・無ければ本文ハッシュの合成キー・ADR-046）。

    url（空白除去後）があればそれを採用する。無ければ本文 text の SHA-256 先頭 16 桁から
    `user://<hash>` を合成する。ハッシュ対象は **text（要約前の原文）** にする＝同じ本文を 2 回
    投入したら同じ url になり UNIQUE(url) ＋ UPSERT で 1 行に収束する（冪等）。要約は LLM の
    非決定的出力なので、summary をハッシュ対象にすると同一本文でも url が割れて二重取り込みになる。
    """
    if url and url.strip():
        return url.strip()
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return f"{_USER_URL_PREFIX}{digest}"


async def ingest_user_news(
    *, text: str, url: str | None = None, code: str | None = None
) -> dict[str, Any]:
    """ユーザー貼付テキストを要約して統合コーパス news へ取り込み、確定行を返す（ADR-046）。

    流れ: (1) summarize_article で本文を 2〜3 行に要約（ADR-014・LLM は要約のみ）→ (2) タグ解決
    （_resolve_user_tags）＋保存 url 決定（_user_news_url の合成キー）→ (3) news 行を組み立て
    UPSERT → (4) get_news_by_url で id/fetched_at 補完済みの確定行を読み直して返す。

    要約失敗（LLM 例外/タイムアウト）はここでは握らず**呼び出し側（router）へ伝播**する＝router
    が 502 に翻訳する（チャットと違い無人通知はしない）。本文は保存せず要約と url のみ（ADR-020）。

    Args:
        text: ユーザーが貼り付けた記事本文（要約対象・必須）。
        url: 元記事 URL（任意）。空なら本文ハッシュの合成キー（user://…）を url に使う。
        code: 銘柄コード（任意）。指定で銘柄層、未指定で市況層（_resolve_user_tags）。

    Returns:
        取り込んだ news 1 行の素 dict（id/level/code/sector17_code/category/source/url/title/
        summary/published_at/fetched_at/extraction_status）。読み直しに失敗した場合は組み立て row。
    """
    summary = await summarize_article(text)  # 失敗は握らず router へ伝播（502 翻訳）

    tags = _resolve_user_tags(code)
    news_url = _user_news_url(url, text)
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    row: dict[str, Any] = {
        **tags,
        # 市況層は表示分類が要るので既定ラベル。銘柄層は code が分類なので category は None。
        "category": "ユーザー投入" if tags["level"] == "market" else None,
        "url": news_url,
        "title": None,  # 貼付テキストにタイトルは無い（要約のみ）
        "summary": summary,
        "published_at": today,  # 投入日（UTC 'YYYY-MM-DD'）
        "fetched_at": None,  # upsert_news が UTC now を補う
        "extraction_status": "summarized",  # ユーザー本文は要約済み相当
    }

    with get_engine().begin() as conn:
        repo.upsert_news(conn, [row])
        saved = repo.get_news_by_url(conn, news_url)
    return saved or row
