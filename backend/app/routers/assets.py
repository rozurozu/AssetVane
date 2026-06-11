"""資産概要・現金・外部資産の REST ルータ（Phase 2／phase2-spec.md §5）。

GET/PUT /cash, GET/POST /external-assets, PUT/DELETE /external-assets/{id},
GET /asset-overview。
比率・weight・deviation は 0..1（spec 単位約束）。
is_delayed は Free 12週遅延（ADR-008）で True 固定。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import Connection

from app.db import repo
from app.db.engine import get_conn
from app.services.fund_holdings import value_fund_holdings
from app.services.policy import get_policy
from app.services.portfolio import portfolio_deviations, value_holdings
from app.services.us_holdings import value_us_holdings

router = APIRouter(tags=["assets"])


# ---------------------------------------------------------------------------
# Pydantic モデル（spec §5 P2-3・P2-4・P2-7 の TS 型と 1:1）
# ---------------------------------------------------------------------------


class CashOut(BaseModel):
    """spec §5 P2-3 Cash。"""

    id: int
    balance: float
    updated_at: str | None = None


class CashIn(BaseModel):
    """spec §5 P2-3 CashInput（PUT /cash body）。"""

    balance: float


class ExternalAssetOut(BaseModel):
    """spec §5 P2-4 ExternalAsset。"""

    id: int
    name: str
    category: str | None = None
    value: float | None = None
    proxy_symbol: str | None = None
    monthly_contribution: float | None = None
    as_of: str | None = None


class ExternalAssetIn(BaseModel):
    """spec §5 P2-4 ExternalAssetInput（POST/PUT body）。"""

    name: str
    category: str | None = None
    value: float
    proxy_symbol: str | None = None
    monthly_contribution: float | None = None
    as_of: str | None = None


class OkOut(BaseModel):
    """単純な成功応答 {ok: true}（DELETE 等・他ルートと同じく response_model を付ける）。"""

    ok: bool


class AllocationSliceOut(BaseModel):
    """spec §5 P2-7 AllocationSlice。"""

    name: str  # "株式" | "現金" | "投資信託" | "外部資産"
    value: float
    weight: float  # 総資産内 0..1


class DeviationOut(BaseModel):
    """spec §5 P2-7 Deviation（portfolio ルータと共用するが独立定義）。"""

    kind: str  # "max_position" | "cash_ratio" | "sector_cap"
    label: str
    current: float  # 0..1
    limit: float  # 0..1
    breached: bool


class AssetSnapshotPointOut(BaseModel):
    """spec §5 P2-7 AssetSnapshotPoint（資産推移スパークライン）。"""

    date: str
    total_value: float


class AssetOverviewOut(BaseModel):
    """spec §5 P2-7 AssetOverview。"""

    as_of: str | None = None
    is_delayed: bool
    plan: str  # "free"
    total_value: float
    stock_value: float
    cash_value: float
    external_value: float
    fund_value: float  # 投信評価額合計（最新 NAV・ADR-054）
    us_stock_value: float  # 米株評価額合計（最新終値×USDJPY・ADR-057）
    pnl: float
    allocation: list[AllocationSliceOut]
    policy_targets: dict[str, Any]  # target_cash_ratio / max_position_weight
    deviations: list[DeviationOut]
    trend: list[AssetSnapshotPointOut]  # 日次総資産推移


# ---------------------------------------------------------------------------
# エンドポイント
# ---------------------------------------------------------------------------


@router.get("/cash", response_model=CashOut)
def get_cash(conn: Connection = Depends(get_conn)) -> CashOut:
    """現在の待機現金残高を返す（spec P2-3）。未登録時は 404。"""
    row = repo.get_cash(conn)
    if row is None:
        raise HTTPException(status_code=404, detail="現金残高が未登録です。")
    return CashOut(**row)


@router.put("/cash", response_model=CashOut)
def put_cash(body: CashIn) -> CashOut:
    """現金残高を更新して返す（spec P2-3・ADR-002）。"""
    row = repo.upsert_cash(body.balance)
    return CashOut(**row)


@router.get("/external-assets", response_model=list[ExternalAssetOut])
def list_external_assets(conn: Connection = Depends(get_conn)) -> list[ExternalAssetOut]:
    """外部資産一覧を返す（spec P2-4）。"""
    rows = repo.list_external_assets(conn)
    return [ExternalAssetOut(**r) for r in rows]


@router.post("/external-assets", response_model=ExternalAssetOut, status_code=201)
def create_external_asset(
    body: ExternalAssetIn,
    conn: Connection = Depends(get_conn),
) -> ExternalAssetOut:
    """外部資産を作成して返す（spec P2-4・ADR-002）。"""
    asset_id = repo.insert_external_asset(body.model_dump(exclude_none=False))
    rows = repo.list_external_assets(conn)
    row = next((r for r in rows if r["id"] == asset_id), None)
    if row is None:
        raise HTTPException(status_code=500, detail="外部資産の作成に失敗しました。")
    return ExternalAssetOut(**row)


@router.put("/external-assets/{asset_id}", response_model=ExternalAssetOut)
def update_external_asset(
    asset_id: int,
    body: ExternalAssetIn,
) -> ExternalAssetOut:
    """外部資産を更新して返す（spec P2-4）。"""
    updated = repo.update_external_asset(asset_id, body.model_dump(exclude_none=False))
    if updated is None:
        raise HTTPException(status_code=404, detail=f"外部資産 {asset_id} は存在しません。")
    return ExternalAssetOut(**updated)


@router.delete("/external-assets/{asset_id}", response_model=OkOut)
def delete_external_asset(asset_id: int) -> dict[str, bool]:
    """外部資産を削除して {ok: true} を返す（spec P2-4）。"""
    ok = repo.delete_external_asset(asset_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"外部資産 {asset_id} は存在しません。")
    return {"ok": True}


@router.get("/asset-overview", response_model=AssetOverviewOut)
def get_asset_overview(conn: Connection = Depends(get_conn)) -> AssetOverviewOut:
    """資産全体像を返す（spec P2-7）。

    株式評価額（holdings × 最新 close）＋ 現金 ＋ 外部資産 ＋ 投信評価額（最新 NAV・ADR-054）
    の合計。投信の含み損益も pnl に合算する。
    deviations は quant の compute_deviations（単一関数）が計算（決定6）。
    sector_weights は株式内 0..1 ベースで統一（注記: 全資産内比率ではなく株式内）。
    Free 12週遅延（ADR-008）: is_delayed=True 固定。
    """
    # --- 先頭ポートフォリオを取得 ---
    portfolios = repo.list_portfolios(conn)
    portfolio_id: int | None = portfolios[0]["portfolio_id"] if portfolios else None

    # --- 株式評価額の計算 ---
    stock_value = 0.0
    pnl = 0.0
    as_of: str | None = None
    holdings_valued: list[dict[str, Any]] = []

    if portfolio_id is not None:
        holdings_rows = repo.list_holdings(conn, portfolio_id)
        codes = [h["code"] for h in holdings_rows]
        if codes:
            latest_closes = repo.get_latest_closes(conn, codes)
            holdings_valued = value_holdings(holdings_rows, latest_closes)
            for h in holdings_valued:
                if h.get("market_value") is not None:
                    stock_value += float(h["market_value"])
                if h.get("unrealized_pnl") is not None:
                    pnl += float(h["unrealized_pnl"])
            as_of = repo.get_max_daily_date(conn)

    # --- 現金 ---
    cash_row = repo.get_cash(conn)
    cash_value = float(cash_row["balance"]) if cash_row else 0.0

    # --- 外部資産 ---
    ext_rows = repo.list_external_assets(conn)
    external_value = sum(float(r["value"]) for r in ext_rows if r.get("value") is not None)

    # --- 投信評価額（最新 NAV・ADR-054）---
    fund_value = 0.0
    if portfolio_id is not None:
        fund_rows = repo.list_fund_holdings(conn, portfolio_id)
        isins = [f["isin"] for f in fund_rows]
        if isins:
            latest_navs = repo.get_latest_fund_navs(conn, isins)
            fund_valued = value_fund_holdings(fund_rows, latest_navs)
            for f in fund_valued:
                if f.get("market_value") is not None:
                    fund_value += float(f["market_value"])
                if f.get("unrealized_pnl") is not None:
                    pnl += float(f["unrealized_pnl"])

    # --- 米株評価額（最新終値×USDJPY・ADR-057）---
    us_stock_value = 0.0
    us_rows = repo.list_us_holdings(conn)
    if us_rows:
        us_symbols = [h["symbol"] for h in us_rows]
        us_closes = repo.get_latest_us_closes(conn, us_symbols)
        us_fx_row = repo.get_latest_fx_rate(conn, "USDJPY")
        us_fx_rate = float(us_fx_row["rate"]) if us_fx_row is not None else None
        us_valued = value_us_holdings(us_rows, us_closes, us_fx_rate)
        for u in us_valued:
            if u.get("market_value_jpy") is not None:
                us_stock_value += float(u["market_value_jpy"])
            if u.get("unrealized_pnl_jpy") is not None:
                pnl += float(u["unrealized_pnl_jpy"])

    total_value = stock_value + cash_value + external_value + fund_value + us_stock_value

    # --- allocation スライス（総資産内 0..1）---
    def _weight(v: float) -> float:
        return v / total_value if total_value > 0 else 0.0

    allocation = [
        AllocationSliceOut(name="株式", value=stock_value, weight=_weight(stock_value)),
        AllocationSliceOut(name="現金", value=cash_value, weight=_weight(cash_value)),
        AllocationSliceOut(name="投資信託", value=fund_value, weight=_weight(fund_value)),
        AllocationSliceOut(name="外部資産", value=external_value, weight=_weight(external_value)),
        AllocationSliceOut(name="米国株", value=us_stock_value, weight=_weight(us_stock_value)),
    ]

    # --- policy と deviations ---
    policy = get_policy(conn)

    # deviations は metrics と同値にするため共有ヘルパで計算（決定6・B-12: 計算 1 か所）。
    deviations = portfolio_deviations(conn, portfolio_id) if portfolio_id is not None else []

    # --- 資産推移トレンド（asset_snapshots） ---
    snapshots = repo.get_asset_snapshots(conn, limit=365)
    trend = [
        AssetSnapshotPointOut(date=s["date"], total_value=float(s["total_value"] or 0))
        for s in snapshots
    ]

    policy_targets = {
        "target_cash_ratio": policy.get("target_cash_ratio"),
        "max_position_weight": policy.get("max_position_weight"),
    }

    return AssetOverviewOut(
        as_of=as_of,
        is_delayed=True,  # Free 12週遅延（ADR-008）
        plan="free",
        total_value=total_value,
        stock_value=stock_value,
        cash_value=cash_value,
        external_value=external_value,
        fund_value=fund_value,
        us_stock_value=us_stock_value,
        pnl=pnl,
        allocation=allocation,
        policy_targets=policy_targets,
        deviations=[DeviationOut(**d) for d in deviations],
        trend=trend,
    )
