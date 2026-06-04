"""株価・銘柄の REST ルータ（Phase 0／docs/api.md §1）。

GET /stocks, GET /stocks/{code}, GET /quotes/{code}。読み取り専用で DB から返すだけ。
取得（J-Quants → DB）は CLI バックフィル（app/scripts/backfill.py）が担う。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import Connection

from app.db import repo
from app.db.engine import get_conn

router = APIRouter(tags=["stocks"])


class Stock(BaseModel):
    code: str
    company_name: str | None = None
    sector33_code: str | None = None
    sector17_code: str | None = None
    market_code: str | None = None
    is_etf: int | None = None


class Quote(BaseModel):
    date: str
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: float | None = None
    adj_close: float | None = None


@router.get("/stocks", response_model=list[Stock])
def list_stocks(
    q: str | None = Query(default=None, description="コード/銘柄名の部分一致"),
    conn: Connection = Depends(get_conn),
) -> list[Stock]:
    """銘柄一覧を検索して返す（docs/api.md §1）。"""
    return [Stock(**row) for row in repo.list_stocks(conn, q)]


@router.get("/stocks/{code}", response_model=Stock)
def get_stock(code: str, conn: Connection = Depends(get_conn)) -> Stock:
    """銘柄詳細を返す。未取得なら 404（docs/api.md §1）。"""
    row = repo.get_stock(conn, code)
    if row is None:
        raise HTTPException(status_code=404, detail=f"銘柄 {code} は未取得です。")
    return Stock(**row)


@router.get("/quotes/{code}", response_model=list[Quote])
def get_quotes(
    code: str,
    from_: str | None = Query(default=None, alias="from", description="開始日 YYYY-MM-DD"),
    to: str | None = Query(default=None, description="終了日 YYYY-MM-DD"),
    conn: Connection = Depends(get_conn),
) -> list[Quote]:
    """チャート用の日足（docs/api.md §1）。date 昇順。time への対応付けはフロントの薄い責務。"""
    return [Quote(**row) for row in repo.get_quotes(conn, code, from_, to)]
