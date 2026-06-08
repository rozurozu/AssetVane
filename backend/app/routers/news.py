"""ニュースの REST ルータ（GET/POST/DELETE /news・ADR-046/047 / docs/api.md）。

設計の真実: docs/decisions.md ADR-046（ユーザー貼付テキストを要約して統合コーパス news へ
取り込む）・ADR-047（ニュース一覧 API）・ADR-005（DB は FastAPI だけ）・ADR-020（本文は持たない）。

HTTP 入出力のみを担う薄い層（要約・タグ解決・取り込みは services.news.ingest_user_news、
クエリは repo）。
- GET は統合コーパス news を level/since/limit で読み（list_news・既定 since は直近 30 日）、
  空でも 200 で items=[] を返す。
- POST は貼付テキストを要約して取り込む口（async＝LLM を await する・ADR-014 は要約のみ）。
  要約失敗（LLM 例外/タイムアウト）は境界で 502 に翻訳する。
- DELETE は source='user' の行のみ削除する（自動取得分は消さない・repo の安全弁）。対象なしは 404。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import Connection

from app.db import repo
from app.db.engine import get_conn
from app.services.news import ingest_user_news

router = APIRouter(tags=["news"])

# GET の既定（指定が無いときの取得窓・件数上限）。
_DEFAULT_SINCE_DAYS = 30
_DEFAULT_LIMIT = 100


# ---------------------------------------------------------------------------
# Pydantic モデル（lib/api.ts の型と 1:1・本文は持たず要約＋URL のみ＝ADR-020）
# ---------------------------------------------------------------------------


class NewsItem(BaseModel):
    """ニュース 1 件（統合コーパス news の 1 行・ADR-044/046）。"""

    id: int
    level: str  # 'stock'/'sector'/'market'/'user' の階層タグ
    code: str | None = None  # 銘柄層の銘柄コード（他層は None）
    sector17_code: str | None = None  # セクター層の S17 業種コード（他層は None）
    category: str | None = None  # 市況層の表示分類（他層は None）
    source: str | None = None  # 'news'/'user'/'disclosure' 等（取り込み元）
    url: str
    title: str | None = None
    summary: str | None = None
    published_at: str | None = None


class NewsListResponse(BaseModel):
    """GET /news のレスポンス。台帳が空でも items=[]。"""

    items: list[NewsItem] = []


class NewsIngestInput(BaseModel):
    """POST /news のリクエスト（ユーザー貼付テキストの取り込み・ADR-046）。"""

    text: str = Field(min_length=1, description="要約対象の記事本文（貼付テキスト）")
    url: str | None = None  # 元記事 URL（任意・無ければ本文ハッシュの合成キー）
    code: str | None = None  # 銘柄コード（任意・指定で銘柄層・未指定で市況層）


class DeleteResult(BaseModel):
    """DELETE /news/{id} のレスポンス。"""

    ok: bool


# ---------------------------------------------------------------------------
# 整形ヘルパ（dict → Pydantic は router の責務）
# ---------------------------------------------------------------------------


def _to_item(row: dict[str, Any]) -> NewsItem:
    """news 行（repo の素 dict）を NewsItem に整形する（本文は持たない・ADR-020）。"""
    return NewsItem(
        id=int(row["id"]),
        level=row["level"],
        code=row.get("code"),
        sector17_code=row.get("sector17_code"),
        category=row.get("category"),
        source=row.get("source"),
        url=row["url"],
        title=row.get("title"),
        summary=row.get("summary"),
        published_at=row.get("published_at"),
    )


# ---------------------------------------------------------------------------
# エンドポイント
# ---------------------------------------------------------------------------


@router.get("/news", response_model=NewsListResponse)
def list_news(
    level: str | None = Query(default=None, description="階層タグ（stock/sector/market/user）"),
    since: str | None = Query(default=None, description="発行日下限 'YYYY-MM-DD'（既定=直近30日）"),
    limit: int = Query(default=_DEFAULT_LIMIT, description="件数上限"),
    conn: Connection = Depends(get_conn),
) -> NewsListResponse:
    """統合コーパス news を発行日降順で返す（ADR-046/047）。

    since 未指定なら直近 30 日を既定窓にする。空でも 200 で items=[]（一覧 UI が壊れない）。
    """
    eff_since = since or (datetime.now(UTC) - timedelta(days=_DEFAULT_SINCE_DAYS)).strftime(
        "%Y-%m-%d"
    )
    rows = repo.list_news(conn, level=level, since=eff_since, limit=limit)
    return NewsListResponse(items=[_to_item(r) for r in rows])


@router.post("/news", response_model=NewsItem)
async def create_news(req: NewsIngestInput) -> NewsItem:
    """ユーザー貼付テキストを要約して取り込み、確定行を返す（ADR-046）。

    要約（LLM）を await するため async（ADR-014 は要約のみ・数値計算はしない）。要約失敗
    （LLM 例外/タイムアウト）は境界で 502 に翻訳する（チャットと違い無人通知はしない）。
    """
    try:
        saved = await ingest_user_news(text=req.text, url=req.url, code=req.code)
    except Exception as exc:  # noqa: BLE001 — 要約失敗（上流 LLM）を 502 に翻訳する境界
        raise HTTPException(
            status_code=502, detail="ニュースの要約に失敗しました。再試行してください。"
        ) from exc
    return _to_item(saved)


@router.delete("/news/{news_id}", response_model=DeleteResult)
def delete_news(news_id: int) -> DeleteResult:
    """ユーザー投入（source='user'）の news を削除する（自動取得分は消さない・ADR-046）。

    対象が無い（id 不在 or 自動取得分）場合は 404 に翻訳する（誤削除を黙って成功にしない）。
    """
    deleted = repo.delete_user_news(news_id)
    if deleted == 0:
        raise HTTPException(status_code=404, detail="削除対象（ユーザー投入）が見つかりません。")
    return DeleteResult(ok=True)
