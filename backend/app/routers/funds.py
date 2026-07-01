"""投資信託 REST ルータ（ADR-054・取引→導出は ADR-019）。

GET/POST /funds・DELETE /funds/{isin}、GET/POST/PUT/DELETE /fund-transactions、
GET /fund-holdings、GET /funds/{isin}/nav-series。
fund_holdings は fund_transactions から導出（ADR-019/054）。AI は数値を計算しない（ADR-014）。
DB に触れるのは FastAPI だけ（ADR-005）。
基準価額・取得単価・nav は 10,000 口あたりの円（評価額 = units/10000 * nav）。
weight は投信内 0..1（UI でのみ ×100）。is_delayed フラグは持たない（投信 NAV は遅延なし）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import Connection

from app.db import repo
from app.db.engine import get_conn, get_engine
from app.services.fund_holdings import recalc_fund_holdings, value_fund_holdings

router = APIRouter(tags=["funds"])


# ---------------------------------------------------------------------------
# Pydantic モデル（frontend / 結合契約と 1:1・ADR-054）
# ---------------------------------------------------------------------------


class FundOut(BaseModel):
    """投信マスタ 1 行（ADR-054）。"""

    isin: str
    name: str
    assoc_code: str | None = None
    updated_at: str | None = None


class FundIn(BaseModel):
    """POST /funds の body（ADR-054）。

    assoc_code（協会コード）は NAV 取得に必須（投信総合検索ライブラリーの NAV CSV は
    associFundCd が無いと空レスポンスになる）。未指定は Pydantic の必須検証で 422 になる。
    """

    isin: str
    name: str
    assoc_code: str  # NAV 取得に必須（associFundCd）。Optional にしない。


class OkOut(BaseModel):
    """単純な成功応答 {ok: true}（DELETE 等・他ルートと同形）。"""

    ok: bool


class FundTransactionOut(BaseModel):
    """投信取引 1 行（ADR-054・ADR-019）。"""

    id: int
    portfolio_id: int
    isin: str
    side: str  # 'buy' / 'sell'
    units: float
    price: float  # 約定基準価額（10,000 口あたりの円）
    fee: float | None = None
    traded_at: str  # 約定日 YYYY-MM-DD


class FundTransactionIn(BaseModel):
    """POST/PUT /fund-transactions の body（ADR-054）。"""

    portfolio_id: int
    isin: str
    side: str  # 'buy' / 'sell'
    units: float
    price: float  # 約定基準価額（10,000 口あたりの円）
    fee: float | None = None
    traded_at: str  # 約定日 YYYY-MM-DD


class FundHoldingOut(BaseModel):
    """投信保有 1 行（最新 NAV で評価額・含み損益付き・ADR-054）。"""

    isin: str
    name: str | None = None
    units: float
    avg_cost: float | None = None  # 移動平均取得単価（10,000 口あたりの円）
    last_nav: float | None = None  # 最新基準価額（10,000 口あたりの円）
    nav_date: str | None = None  # 最新 NAV の基準日
    market_value: float | None = None  # units/10000 * nav
    unrealized_pnl: float | None = None
    weight: float | None = None  # 投信内 0..1（UI でのみ ×100）


class FundNavPointOut(BaseModel):
    """NAV 時系列の 1 点（nav-series チャート用・ADR-054）。"""

    date: str
    nav: float | None = None


# ---------------------------------------------------------------------------
# ヘルパ: fund_holdings レスポンス構築
# ---------------------------------------------------------------------------


def _build_fund_holdings(conn: Connection, portfolio_id: int) -> list[FundHoldingOut]:
    """fund_holdings を最新 NAV で評価して FundHoldingOut のリストにする（ADR-054）。

    DB から fund_holdings ＋ latest_navs を引き、value_fund_holdings で評価額を付与する
    （10,000 口換算は service が担う）。
    """
    holdings_rows = repo.list_fund_holdings(conn, portfolio_id)
    isins = [h["isin"] for h in holdings_rows]
    latest_navs = repo.get_latest_fund_navs(conn, isins) if isins else {}
    valued = value_fund_holdings(holdings_rows, latest_navs)
    return [FundHoldingOut(**h) for h in valued]


# ---------------------------------------------------------------------------
# funds マスタ
# ---------------------------------------------------------------------------


@router.get("/funds", response_model=list[FundOut])
def list_funds(conn: Connection = Depends(get_conn)) -> list[FundOut]:
    """投信マスタ一覧を返す（ADR-054）。"""
    return [FundOut(**r) for r in repo.list_funds(conn)]


@router.post("/funds", response_model=FundOut, status_code=201)
def create_fund(body: FundIn) -> FundOut:
    """投信マスタを登録（既存 isin は更新）して返す（ADR-054・ADR-002）。

    協会コード（assoc_code）は NAV 取得に必須（投信総合検索ライブラリーの NAV CSV は
    associFundCd が無いと空レスポンスになる）。未指定は FundIn の必須検証で 422 に弾かれる。
    """
    row = repo.upsert_fund(body.isin, body.name, body.assoc_code)
    return FundOut(**row)


@router.delete("/funds/{isin}", response_model=OkOut)
def delete_fund(isin: str) -> dict[str, bool]:
    """投信マスタを削除して {ok: true} を返す（ADR-054）。存在しなければ 404。"""
    ok = repo.delete_fund(isin)
    if not ok:
        raise HTTPException(status_code=404, detail=f"投信 {isin} は存在しません。")
    return {"ok": True}


@router.get("/funds/{isin}/nav-series", response_model=list[FundNavPointOut])
def get_fund_nav_series(
    isin: str,
    limit: int = Query(default=365, ge=1, description="最新 N 件（date 昇順）"),
    conn: Connection = Depends(get_conn),
) -> list[FundNavPointOut]:
    """指定 isin の NAV 時系列を date 昇順・最新 limit 件で返す（ADR-054）。"""
    return [FundNavPointOut(**r) for r in repo.get_fund_nav_series(conn, isin, limit)]


# ---------------------------------------------------------------------------
# fund_transactions（取引→導出・ADR-019）
# ---------------------------------------------------------------------------


@router.get("/fund-transactions", response_model=list[FundTransactionOut])
def list_fund_transactions_endpoint(
    portfolio_id: int = Query(description="対象ポートフォリオ"),
    conn: Connection = Depends(get_conn),
) -> list[FundTransactionOut]:
    """投信取引履歴を新しい順で返す（ADR-054・ADR-019）。

    list_fund_transactions は再導出用に昇順だが、履歴表示は新しい順
    （traded_at 降順・同日は id 降順）で返す（株式 transactions と同方針）。
    """
    txns = repo.list_fund_transactions(conn, portfolio_id)
    txns_sorted = sorted(txns, key=lambda t: (t["traded_at"], t["id"]), reverse=True)
    return [FundTransactionOut(**t) for t in txns_sorted]


@router.post("/fund-transactions", response_model=list[FundHoldingOut], status_code=201)
def post_fund_transaction(body: FundTransactionIn) -> list[FundHoldingOut]:
    """投信取引を記録し fund_holdings を再計算して評価額付き保有を返す（ADR-054・ADR-019）。

    1. fund_transactions に INSERT。
    2. recalc_fund_holdings で fund_holdings を入れ替え（ADR-019）。
    3. 更新後 fund_holdings を最新 NAV 評価額付きで返す。
    1〜3 は同じトランザクション内で行い、中間状態を残さない（株式 POST /transactions と同構造）。
    """
    row: dict[str, Any] = {
        "portfolio_id": body.portfolio_id,
        "isin": body.isin,
        "side": body.side,
        "units": body.units,
        "price": body.price,
        "traded_at": body.traded_at,
    }
    if body.fee is not None:
        row["fee"] = body.fee

    with get_engine().begin() as conn:
        repo.insert_fund_transaction(conn, row)
        recalc_fund_holdings(conn, body.portfolio_id)
        return _build_fund_holdings(conn, body.portfolio_id)


@router.put("/fund-transactions/{txn_id}", response_model=list[FundHoldingOut])
def put_fund_transaction(txn_id: int, body: FundTransactionIn) -> list[FundHoldingOut]:
    """投信取引を更新し fund_holdings を再計算して返す（ADR-054・ADR-019）。

    1. 存在確認（無ければ 404）。
    2. fund_transactions を UPDATE。
    3. recalc_fund_holdings で fund_holdings を入れ替え。
    4. 更新後 fund_holdings を評価額付きで返す。
    2〜4 は同じトランザクション内で行い、中間状態を残さない。
    """
    row: dict[str, Any] = {
        "isin": body.isin,
        "side": body.side,
        "units": body.units,
        "price": body.price,
        "fee": body.fee,
        "traded_at": body.traded_at,
    }

    with get_engine().begin() as conn:
        existing = repo.get_fund_transaction(conn, txn_id)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"投信取引 {txn_id} は存在しません。")
        # recalc は取引の**実所属** portfolio で（body.portfolio_id ではない・#26）。update は
        # portfolio_id を更新しないので、body の別 portfolio_id で recalc すると実所属側の
        # fund_holdings が取引と乖離する（delete_fund_transaction_endpoint と同型に揃える）。
        pid = int(existing["portfolio_id"])
        repo.update_fund_transaction(conn, txn_id, row)
        recalc_fund_holdings(conn, pid)
        return _build_fund_holdings(conn, pid)


@router.delete("/fund-transactions/{txn_id}", response_model=list[FundHoldingOut])
def delete_fund_transaction_endpoint(txn_id: int) -> list[FundHoldingOut]:
    """投信取引を削除し fund_holdings を再計算して返す（ADR-054・ADR-019）。

    1. 存在確認＆所属 portfolio_id を取得（無ければ 404）。
    2. fund_transactions を DELETE。
    3. recalc_fund_holdings で fund_holdings を入れ替え。
    4. 更新後 fund_holdings を評価額付きで返す。
    2〜4 は同じトランザクション内で行い、中間状態を残さない。
    """
    with get_engine().begin() as conn:
        existing = repo.get_fund_transaction(conn, txn_id)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"投信取引 {txn_id} は存在しません。")
        pid = int(existing["portfolio_id"])

        repo.delete_fund_transaction(conn, txn_id)
        recalc_fund_holdings(conn, pid)
        return _build_fund_holdings(conn, pid)


@router.get("/fund-holdings", response_model=list[FundHoldingOut])
def get_fund_holdings(
    portfolio_id: int = Query(description="対象ポートフォリオ"),
    conn: Connection = Depends(get_conn),
) -> list[FundHoldingOut]:
    """投信保有を最新 NAV で評価して返す（評価額・含み損益付き・ADR-054）。"""
    return _build_fund_holdings(conn, portfolio_id)
