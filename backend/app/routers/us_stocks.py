"""米国株・銘柄の REST ルータ（Phase 7(B-1)／提示専用＝ADR-039(B)・ADR-055）。

GET /us-stocks, GET /us-stocks/screen, GET /us-stocks/{symbol}, GET /us-quotes/{symbol}。
読み取り専用。取得（yfinance → DB）は夜間バッチ（sync_us_universe / fetch_us_quotes /
fetch_us_fundamentals / calc_us_valuation）が担う。日本株 routers/stocks.py をミラーしつつ
code→symbol・sector33_code→gics_sector に読み替える（市場分離＝ADR-031）。B-1 は提示専用で
FX 換算・保有登録は B-2 送り（currency 列も持たない）。数値は USD（ドル）。
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import Connection

from app.db import repo
from app.db.engine import get_conn

router = APIRouter(tags=["us-stocks"])


class UsStock(BaseModel):
    """米株マスタ 1 行（us_stocks・日本株 Stock のミラー）。"""

    symbol: str
    company_name: str | None = None
    gics_sector: str | None = None
    industry: str | None = None
    is_etf: int | None = None


class UsScreenCriteria(BaseModel):
    """米株スクリーニング条件（クエリ束縛・ScreenCriteria のミラー・ADR-031/055）。

    比率は 0..1（×100 は UI 側・ADR-008）。日本株の sector33_code 絞り込みではなく
    gics_sector（Yahoo `.info.sector` 文字列の完全一致）で絞る。各 *_growth_yoy は素データの
    都合で NULL になり得るが、min/max 比較で自然に除外される（repo.screen_us_stocks）。
    """

    per_min: float | None = None
    per_max: float | None = None
    pbr_min: float | None = None
    pbr_max: float | None = None
    market_cap_min: float | None = None  # USD
    market_cap_max: float | None = None
    dividend_yield_min: float | None = None  # 0..1
    dividend_yield_max: float | None = None
    roe_min: float | None = None  # 0..1
    roe_max: float | None = None
    operating_margin_min: float | None = None  # 0..1
    operating_margin_max: float | None = None
    net_margin_min: float | None = None  # 0..1
    net_margin_max: float | None = None
    revenue_growth_yoy_min: float | None = None  # 0..1 基準の比率
    revenue_growth_yoy_max: float | None = None
    op_growth_yoy_min: float | None = None
    op_growth_yoy_max: float | None = None
    profit_growth_yoy_min: float | None = None
    profit_growth_yoy_max: float | None = None
    eps_growth_yoy_min: float | None = None
    eps_growth_yoy_max: float | None = None
    gics_sector: str | None = None  # GICS 相当セクター（完全一致）
    exclude_etf: bool = False
    gics_sector_pctile_max: float | None = None  # GICS 内で安い割合（0..1）
    market_cap_rank_max: int | None = None  # 時価総額 上位 N
    sort_by: (
        Literal[
            "per",
            "pbr",
            "market_cap",
            "dividend_yield",
            "roe",
            "operating_margin",
            "net_margin",
            "revenue_growth_yoy",
            "op_growth_yoy",
            "profit_growth_yoy",
            "eps_growth_yoy",
            "gics_sector_pctile",
            "market_cap_rank",
            "symbol",
        ]
        | None
    ) = None
    sort_dir: Literal["asc", "desc"] | None = None
    limit: int = 200
    offset: int = 0


class UsScreenRow(BaseModel):
    """米株スクリーナー 1 行（us_valuation_snapshots × us_stocks ＋ ランク）。ScreenRow ミラー。"""

    symbol: str
    company_name: str | None = None
    gics_sector: str | None = None
    industry: str | None = None
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
    roe: float | None = None
    operating_margin: float | None = None
    net_margin: float | None = None
    revenue_growth_yoy: float | None = None
    op_growth_yoy: float | None = None
    profit_growth_yoy: float | None = None
    eps_growth_yoy: float | None = None
    gics_sector_pctile: float | None = None
    market_cap_rank: int | None = None


class UsValuationSnapshot(BaseModel):
    """米株 1 銘柄のバリュエーション事実（PER/PBR/ROE/利益率/成長率＋GICS 内ランク・ADR-014/048）。

    数値は夜間 calc_us_valuation が焼いた事実で verdict は持たない。未焼成なら詳細応答では null。
    """

    symbol: str
    company_name: str | None = None
    gics_sector: str | None = None
    industry: str | None = None
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
    roe: float | None = None
    operating_margin: float | None = None
    net_margin: float | None = None
    revenue_growth_yoy: float | None = None
    op_growth_yoy: float | None = None
    profit_growth_yoy: float | None = None
    eps_growth_yoy: float | None = None
    gics_sector_pctile: float | None = None
    market_cap_rank: int | None = None


class UsStockDetail(BaseModel):
    """米株詳細（マスタ＋valuation snapshot）。未焼成なら valuation=null（/stocks/{code} 同型）。"""

    symbol: str
    company_name: str | None = None
    gics_sector: str | None = None
    industry: str | None = None
    is_etf: int | None = None
    valuation: UsValuationSnapshot | None = None


class UsQuote(BaseModel):
    date: str
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: float | None = None
    adj_close: float | None = None


@router.get("/us-stocks", response_model=list[UsStock])
def list_us_stocks(
    q: str | None = Query(default=None, description="symbol/銘柄名の部分一致"),
    conn: Connection = Depends(get_conn),
) -> list[UsStock]:
    """米株マスタ一覧を検索して返す（list_stocks 同型）。"""
    return [UsStock(**row) for row in repo.list_us_stocks(conn, q)]


@router.get("/us-stocks/screen", response_model=list[UsScreenRow])
def screen_us_stocks(
    criteria: Annotated[UsScreenCriteria, Query()],
    conn: Connection = Depends(get_conn),
) -> list[UsScreenRow]:
    """バリュエーションで米株を絞り込む（読み取り時計算・ADR-031/055）。

    /us-stocks/{symbol} より先に宣言する（順序を逆にすると "screen" が {symbol} に食われる）。
    """
    rows = repo.screen_us_stocks(conn, criteria.model_dump(exclude_none=True))
    return [UsScreenRow(**row) for row in rows]


@router.get("/us-stocks/{symbol}", response_model=UsStockDetail)
def get_us_stock(symbol: str, conn: Connection = Depends(get_conn)) -> UsStockDetail:
    """米株詳細（マスタ＋valuation snapshot）を返す。

    マスタ未取得なら 404。マスタはあるが valuation 未焼成なら 200＋valuation=null
    （日本株 /stocks/{code} と同じく「あるものを返す」流儀・spec §1）。
    """
    stock = repo.get_us_stock(conn, symbol)
    if stock is None:
        raise HTTPException(status_code=404, detail=f"米株 {symbol} は未取得です。")
    snapshot = repo.get_us_valuation_snapshot(conn, symbol)
    return UsStockDetail(
        **stock,
        valuation=UsValuationSnapshot(**snapshot) if snapshot is not None else None,
    )


@router.get("/us-quotes/{symbol}", response_model=list[UsQuote])
def get_us_quotes(
    symbol: str,
    from_: str | None = Query(default=None, alias="from", description="開始日 YYYY-MM-DD"),
    to: str | None = Query(default=None, description="終了日 YYYY-MM-DD"),
    conn: Connection = Depends(get_conn),
) -> list[UsQuote]:
    """米株チャート用の日足（get_quotes 同型）。date 昇順。"""
    return [UsQuote(**row) for row in repo.get_us_quotes(conn, symbol, from_, to)]
