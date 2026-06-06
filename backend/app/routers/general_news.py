"""一般ニュースの REST ルータ（GET /general-news・ADR-034 / docs/api.md）。

設計の真実: docs/decisions.md ADR-034・grill-me 合意（3-adr-034-floofy-hoare）・ADR-005。

HTTP 入出力のみを担う薄い層。general_news 台帳（夜間ジョブ fetch_general_news が貯める）を
直近分だけ読み、category 別にグルーピングして返す（Dashboard widget が 1:1 で消費）。
DB に触れるのは FastAPI だけ（ADR-005）。グルーピングは HTTP 寄りの軽い整形なので router で行う。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import Connection

from app.adapters.general_news_config import GENERAL_NEWS_LOOKBACK_DAYS
from app.db import repo
from app.db.engine import get_conn

router = APIRouter(tags=["general-news"])


# ---------------------------------------------------------------------------
# Pydantic モデル（lib/api.ts の型と 1:1）
# ---------------------------------------------------------------------------


class GeneralNewsItem(BaseModel):
    """一般ニュース記事 1 件（本文は持たず要約＋URL のみ＝ADR-020 の流儀）。"""

    url: str
    title: str | None = None
    summary: str | None = None
    published_at: str | None = None
    source_type: str | None = None
    category: str


class GeneralNewsCategory(BaseModel):
    """カテゴリ 1 つ分（label＋その記事リスト）。"""

    label: str
    items: list[GeneralNewsItem] = []


class GeneralNewsResponse(BaseModel):
    """GET /general-news のレスポンス（カテゴリ別グルーピング）。台帳が空でも categories=[]。"""

    categories: list[GeneralNewsCategory] = []


# ---------------------------------------------------------------------------
# エンドポイント
# ---------------------------------------------------------------------------


@router.get("/general-news", response_model=GeneralNewsResponse)
def get_general_news(conn: Connection = Depends(get_conn)) -> GeneralNewsResponse:
    """直近の一般ニュースをカテゴリ別に返す（ADR-034）。

    lookback は取得側と同じ定数で揃え、直近分のみ返す。台帳が空でも 200 で categories=[]
    （widget が壊れない＝dossier の空ドシエ方針に合わせる）。
    """
    since = (datetime.now(UTC) - timedelta(days=GENERAL_NEWS_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    rows = repo.list_general_news(conn, since=since)

    grouped: dict[str, list[GeneralNewsItem]] = {}
    for r in rows:
        grouped.setdefault(r["category"], []).append(
            GeneralNewsItem(
                url=r["url"],
                title=r.get("title"),
                summary=r.get("summary"),
                published_at=r.get("published_at"),
                source_type=r.get("source_type"),
                category=r["category"],
            )
        )
    categories = [GeneralNewsCategory(label=label, items=items) for label, items in grouped.items()]
    return GeneralNewsResponse(categories=categories)
