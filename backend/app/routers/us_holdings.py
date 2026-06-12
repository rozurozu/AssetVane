"""米株保有・取引の REST ルータ（Phase 7(B-2)／ADR-057）。

GET /us-holdings・GET /us-transactions・POST/PUT/DELETE /us-transactions（PUT は C-14＝
tasks/review-2026-06-12.md・JP/投信の編集 PUT のミラー）。
取引登録→holdings 再導出→評価額付き保有を返す形は routers/funds.py の post_fund_transaction と
同構造。評価額（JPY）は value_us_holdings が計算し、router は事実を Pydantic に詰めるだけ
（AI に数値を計算させない・ADR-014）。DB に触れるのは FastAPI だけ（ADR-005）。
単一ユーザー（ADR-001）ゆえ portfolio_id は持たない（us_holdings は global 保有）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import Connection

from app.db import repo
from app.db.engine import get_conn, get_engine
from app.services.us_holdings import recalc_us_holdings, value_us_holdings

router = APIRouter(tags=["us-holdings"])


# ---------------------------------------------------------------------------
# Pydantic モデル（ADR-057・frontend 契約と 1:1）
# ---------------------------------------------------------------------------


class UsTransactionIn(BaseModel):
    """POST/PUT /us-transactions の body（ADR-057・PUT は C-14＝tasks/review-2026-06-12.md）。

    fx_rate 省略時は約定日の fx_rates を自動解決する。それも無ければ 400。
    """

    symbol: str
    side: str  # 'buy' / 'sell'
    shares: float
    price: float  # 約定価格（USD）
    fee: float | None = None
    traded_at: str  # 約定日 YYYY-MM-DD
    fx_rate: float | None = None  # 約定時 USDJPY（省略時は fx_rates から解決）
    note: str | None = None


class UsTransactionOut(BaseModel):
    """米株取引 1 行（ADR-057）。"""

    id: int
    symbol: str
    company_name: str | None = None
    side: str
    shares: float
    price: float
    fee: float | None = None
    traded_at: str
    fx_rate: float
    note: str | None = None


class UsHoldingOut(BaseModel):
    """米株保有 1 行（最新終値＋FX で JPY 評価額・含み損益付き・ADR-057）。"""

    id: int
    symbol: str
    company_name: str | None = None
    gics_sector: str | None = None
    shares: float
    avg_cost: float | None = None  # 移動平均取得単価（USD）
    avg_cost_jpy: float | None = None  # 取得時レート固定の JPY 原価単価
    last_close: float | None = None  # 最新終値（USD）
    close_date: str | None = None  # その終値の営業日
    fx_rate: float | None = None  # 換算に使った USDJPY
    market_value_jpy: float | None = None  # shares × close × fx_rate
    cost_jpy: float | None = None  # shares × avg_cost_jpy
    unrealized_pnl_jpy: float | None = None  # 為替損益込みの含み損益
    weight: float | None = None  # 米株内合計に対する比率 0..1


# ---------------------------------------------------------------------------
# ヘルパ: us_holdings レスポンス構築
# ---------------------------------------------------------------------------


def _build_us_holdings(conn: Connection) -> list[UsHoldingOut]:
    """us_holdings を最新終値＋FX レートで評価して UsHoldingOut のリストにする（ADR-057）。

    DB から us_holdings・最新終値・最新 FX を引き、value_us_holdings で JPY 評価額を付与する。
    FX 未取得でも 200 で返す（評価系は None・value_us_holdings が None 安全）。
    """
    holdings_rows = repo.list_us_holdings(conn)
    symbols = [h["symbol"] for h in holdings_rows]
    latest_closes = repo.get_latest_us_closes(conn, symbols) if symbols else {}
    fx_row = repo.get_latest_fx_rate(conn, "USDJPY")
    fx_rate = float(fx_row["rate"]) if fx_row is not None else None
    valued = value_us_holdings(holdings_rows, latest_closes, fx_rate)
    return [UsHoldingOut(**h) for h in valued]


# ---------------------------------------------------------------------------
# エンドポイント
# ---------------------------------------------------------------------------


@router.get("/us-holdings", response_model=list[UsHoldingOut])
def get_us_holdings(conn: Connection = Depends(get_conn)) -> list[UsHoldingOut]:
    """米株保有一覧を最新終値・USDJPY で JPY 評価して返す（ADR-057）。

    FX 未取得・終値未取得の銘柄も 200 で含め、評価系列は None になる（fund-holdings と同方針）。
    """
    return _build_us_holdings(conn)


@router.get("/us-transactions", response_model=list[UsTransactionOut])
def list_us_transactions(conn: Connection = Depends(get_conn)) -> list[UsTransactionOut]:
    """米株取引履歴を全件返す（新しい順・ADR-057）。

    list_us_transactions は再導出用に昇順だが、履歴表示は traded_at 降順・同日は id 降順
    （list_fund_transactions_endpoint と同方針）。
    """
    txns = repo.list_us_transactions(conn)
    txns_sorted = sorted(txns, key=lambda t: (t["traded_at"], t["id"]), reverse=True)
    return [UsTransactionOut(**t) for t in txns_sorted]


@router.post("/us-transactions", response_model=list[UsHoldingOut], status_code=201)
def post_us_transaction(body: UsTransactionIn) -> list[UsHoldingOut]:
    """米株取引を記録し us_holdings を再計算して JPY 評価額付き保有を返す（ADR-057・ADR-019）。

    1. symbol の存在確認（us_stocks に無ければ 404）。
    2. fx_rate 解決: body.fx_rate があればそれ / 無ければ約定日の fx_rates /
       それも無ければ 400。
    3. us_transactions に INSERT。
    4. recalc_us_holdings で us_holdings を入れ替え（ADR-019）。
    5. 更新後 us_holdings を JPY 評価額付きで返す。
    3〜4 は同じトランザクション内で atomic に行う。
    """
    # side 検証（Pydantic では弾けない enum 値を事前確認）
    if body.side not in {"buy", "sell"}:
        raise HTTPException(status_code=400, detail="side は 'buy' または 'sell' のみ有効です。")

    # symbol の存在確認（us_stocks FK 親の事前チェック）
    with get_engine().connect() as check_conn:
        if repo.get_us_stock(check_conn, body.symbol) is None:
            raise HTTPException(status_code=404, detail=f"未知の米株 symbol: {body.symbol}")

    # fx_rate 解決
    fx_rate: float
    if body.fx_rate is not None:
        fx_rate = body.fx_rate
    else:
        with get_engine().connect() as fx_conn:
            fx_row = repo.get_fx_rate_on(fx_conn, "USDJPY", body.traded_at)
        if fx_row is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "FX レート未取得（先に fetch_fx_rates を回すか fx_rate を明示してください）"
                ),
            )
        fx_rate = float(fx_row["rate"])

    # 取引登録＋holdings 再計算（atomic）
    row: dict = {
        "symbol": body.symbol,
        "side": body.side,
        "shares": body.shares,
        "price": body.price,
        "traded_at": body.traded_at,
        "fx_rate": fx_rate,
    }
    if body.fee is not None:
        row["fee"] = body.fee
    if body.note is not None:
        row["note"] = body.note

    with get_engine().begin() as conn:
        repo.insert_us_transaction(conn, row)
        recalc_us_holdings(conn, body.symbol)
        return _build_us_holdings(conn)


@router.put("/us-transactions/{txn_id}", response_model=list[UsHoldingOut])
def put_us_transaction(txn_id: int, body: UsTransactionIn) -> list[UsHoldingOut]:
    """米株取引を更新し us_holdings を再計算して返す（ADR-057・ADR-019・C-14）。

    JP PUT /transactions・投信 PUT /fund-transactions のミラー（tasks/review-2026-06-12.md C-14）。
    1. 存在確認＆旧 symbol を取得（無ければ 404）。
    2. body.symbol の存在確認（us_stocks に無ければ 404）。
    3. fx_rate 解決: POST と同じ（body 明示 → 約定日の fx_rates → どちらも無ければ 400）。
    4. us_transactions を UPDATE（fee/note は None でもクリアとして書く）。
    5. recalc_us_holdings で us_holdings を入れ替え（ADR-019）。symbol を変更した編集は
       旧 symbol 側も再導出する（米株の recalc は symbol 単位のため・JP は portfolio 単位で不要）。
    6. 更新後 us_holdings を JPY 評価額付きで返す。
    1〜6 は同じトランザクション内で行い、中間状態を残さない。
    """
    # side 検証（Pydantic では弾けない enum 値を事前確認・POST と同型）
    if body.side not in {"buy", "sell"}:
        raise HTTPException(status_code=400, detail="side は 'buy' または 'sell' のみ有効です。")

    with get_engine().begin() as conn:
        existing = repo.get_us_transaction(conn, txn_id)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"米株取引 {txn_id} は存在しません。")
        old_symbol = str(existing["symbol"])

        # symbol の存在確認（us_stocks FK 親の事前チェック・POST と同型）
        if repo.get_us_stock(conn, body.symbol) is None:
            raise HTTPException(status_code=404, detail=f"未知の米株 symbol: {body.symbol}")

        # fx_rate 解決（POST と同じ順: body 明示 → 約定日の fx_rates → 400）
        if body.fx_rate is not None:
            fx_rate = body.fx_rate
        else:
            fx_row = repo.get_fx_rate_on(conn, "USDJPY", body.traded_at)
            if fx_row is None:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "FX レート未取得（先に fetch_fx_rates を回すか fx_rate を明示してください）"
                    ),
                )
            fx_rate = float(fx_row["rate"])

        # 取引更新と us_holdings 再導出を atomic にする（ADR-019）。
        # fee/note は JP/投信 PUT と同様に無条件で書く（None＝クリアできるように）。
        row: dict = {
            "symbol": body.symbol,
            "side": body.side,
            "shares": body.shares,
            "price": body.price,
            "fee": body.fee,
            "traded_at": body.traded_at,
            "fx_rate": fx_rate,
            "note": body.note,
        }
        repo.update_us_transaction(conn, txn_id, row)
        recalc_us_holdings(conn, body.symbol)
        if old_symbol != body.symbol:
            recalc_us_holdings(conn, old_symbol)
        return _build_us_holdings(conn)


@router.delete("/us-transactions/{txn_id}", response_model=list[UsHoldingOut])
def delete_us_transaction(txn_id: int) -> list[UsHoldingOut]:
    """米株取引を削除し us_holdings を再計算して返す（ADR-057・ADR-019）。

    1. 存在確認＆対象 symbol を取得（無ければ 404）。
    2. us_transactions を DELETE。
    3. recalc_us_holdings で us_holdings を入れ替え（全売却なら行が消える）。
    4. 更新後 us_holdings を評価額付きで返す。
    2〜3 は同じトランザクション内で atomic に行う。
    """
    with get_engine().begin() as conn:
        existing = repo.get_us_transaction(conn, txn_id)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"米株取引 {txn_id} は存在しません。")
        symbol = str(existing["symbol"])
        repo.delete_us_transaction(conn, txn_id)
        recalc_us_holdings(conn, symbol)
        return _build_us_holdings(conn)
