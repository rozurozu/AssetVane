"""株価・銘柄の REST ルータ（Phase 0／docs/api.md §1・スクリーナー＝ADR-031）。

GET /stocks, GET /stocks/screen, GET /stocks/{code}, GET /quotes/{code}。読み取り専用。
取得（J-Quants → DB）は夜間バッチ／CLI バックフィルが担う。
"""

from __future__ import annotations

from typing import Annotated, Literal

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


class ScreenCriteria(BaseModel):
    """スクリーニング条件（クエリ束縛・ADR-031）。比率は 0..1（×100 は UI 側・ADR-008）。

    保存フィルタ（screening_filters）はこの形を JSON にして持つが、前方互換のため repo/保存側は
    緩い dict で扱う（テクニカル軸の追加＝TODO に備える）。
    """

    per_min: float | None = None
    per_max: float | None = None
    pbr_min: float | None = None
    pbr_max: float | None = None
    market_cap_min: float | None = None  # 円
    market_cap_max: float | None = None
    dividend_yield_min: float | None = None  # 0..1
    dividend_yield_max: float | None = None
    sector33_code: str | None = None
    market_code: str | None = None
    exclude_etf: bool = False
    per_sector_pctile_max: float | None = None  # 業種内で安い割合（0..1）
    market_cap_rank_max: int | None = None  # 時価総額 上位 N
    sort_by: (
        Literal["per", "pbr", "market_cap", "dividend_yield", "per_sector_pctile", "code"] | None
    ) = None
    sort_dir: Literal["asc", "desc"] | None = None
    limit: int = 200
    offset: int = 0


class ScreenRow(BaseModel):
    """スクリーナー 1 行（valuation_snapshots × stocks ＋ 読み取り時ランク・ADR-031）。"""

    code: str
    company_name: str | None = None
    sector33_code: str | None = None
    market_code: str | None = None
    is_etf: int | None = None
    as_of_date: str | None = None
    close: float | None = None
    eps: float | None = None
    bps: float | None = None
    dividend_per_share: float | None = None
    per: float | None = None
    pbr: float | None = None
    market_cap: float | None = None
    dividend_yield: float | None = None
    per_sector_pctile: float | None = None
    market_cap_rank: int | None = None


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


@router.get("/stocks/screen", response_model=list[ScreenRow])
def screen_stocks(
    criteria: Annotated[ScreenCriteria, Query()],
    conn: Connection = Depends(get_conn),
) -> list[ScreenRow]:
    """バリュエーションで全銘柄を絞り込む（読み取り時計算・ADR-026/031）。

    /stocks/{code} より先に宣言する（順序を逆にすると "screen" が {code} に食われる）。
    """
    rows = repo.screen_stocks(conn, criteria.model_dump(exclude_none=True))
    return [ScreenRow(**row) for row in rows]


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
