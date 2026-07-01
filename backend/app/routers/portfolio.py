"""ポートフォリオ REST ルータ（Phase 2／phase2-spec.md §5）。

GET /portfolios, GET /holdings, POST /transactions,
GET /portfolio/{id}/metrics, POST /portfolio/{id}/optimize。
holdings は transactions から導出（ADR-019）。
AI は数値を計算しない（ADR-014）— 計算は quant 純関数が担う。
DB に触れるのは FastAPI だけ（ADR-005）。
比率・weight・deviation の current/limit は 0..1（spec 単位約束）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import Connection

from app.db import repo
from app.db.engine import get_conn, get_engine
from app.quant import compute_portfolio_metrics, optimize_portfolio
from app.services.holdings import recalc_holdings
from app.services.policy import get_policy
from app.services.portfolio import (
    backtest_portfolio_service,
    build_price_panel,
    current_stock_weights,
    portfolio_deviations,
    value_holdings,
)

router = APIRouter(tags=["portfolio"])


# ---------------------------------------------------------------------------
# Pydantic モデル（spec §5 P2-1〜P2-6 の TS 型と 1:1）
# ---------------------------------------------------------------------------


class PortfolioOut(BaseModel):
    """spec §5 P2-1 Portfolio。"""

    portfolio_id: int
    name: str
    created_at: str | None = None


class ValuationMeta(BaseModel):
    """holdings レスポンスに付ける評価額メタ（spec §5 P2-2）。"""

    as_of: str | None = None
    is_delayed: bool
    plan: str  # "free" 等（Free 12週遅延の説明用）


class HoldingOut(BaseModel):
    """spec §5 P2-2 Holding。"""

    id: int
    code: str
    company_name: str | None = None
    shares: float
    avg_cost: float | None = None
    last_close: float | None = None
    market_value: float | None = None
    unrealized_pnl: float | None = None
    weight: float | None = None  # 株式内 0..1（UI でのみ ×100）


class HoldingsResponse(BaseModel):
    """spec §5 P2-2 HoldingsResponse。"""

    portfolio_id: int
    holdings: list[HoldingOut]
    valuation_meta: ValuationMeta


class TransactionIn(BaseModel):
    """spec §5 P2-2 TransactionInput（POST /transactions の body）。"""

    portfolio_id: int
    code: str
    side: str  # 'buy' / 'sell'
    shares: float
    price: float  # 約定単価
    fee: float | None = None
    traded_at: str  # 約定日 YYYY-MM-DD


class TransactionResult(BaseModel):
    """spec §5 P2-2 TransactionResult（POST/PUT/DELETE /transactions のレスポンス）。"""

    transaction_id: int
    holdings: HoldingsResponse


class TransactionOut(BaseModel):
    """spec §5 P2-2 Transaction（GET /transactions の 1 行）。

    company_name は stocks JOIN で補完（行レベルに名前を焼かない＝ADR-019/repo 規約）。
    """

    id: int
    code: str
    company_name: str | None = None
    side: str  # 'buy' / 'sell'
    shares: float
    price: float
    fee: float | None = None
    traded_at: str  # 約定日 YYYY-MM-DD


class DeviationOut(BaseModel):
    """spec §5 P2-5 Deviation（P2-7 asset-overview と共用）。"""

    kind: str  # "max_position" | "cash_ratio" | "sector_cap"
    label: str
    current: float  # 0..1
    limit: float  # 0..1
    breached: bool


class CorrelationMatrixOut(BaseModel):
    """spec §5 P2-5 CorrelationMatrix。"""

    codes: list[str]
    labels: list[str]
    matrix: list[list[float]]


class PortfolioMetricsOut(BaseModel):
    """spec §5 P2-5 PortfolioMetrics。"""

    portfolio_id: int
    as_of: str | None = None
    is_delayed: bool
    annual_return: float | None = None
    annual_volatility: float | None = None
    sharpe: float | None = None
    max_drawdown: float | None = None
    lookback_days: int | None = None
    correlation: CorrelationMatrixOut
    deviations: list[DeviationOut]


class OptimizeIn(BaseModel):
    """spec §5 P2-6 OptimizeRequest（任意・policy 上書き用）。"""

    target_cash_ratio: float | None = None
    max_position_weight: float | None = None
    sector_caps: dict[str, float] | None = None


class OptimizeWeightOut(BaseModel):
    """spec §5 P2-6 OptimizeWeight。"""

    code: str
    company_name: str | None = None
    current_weight: float | None = None
    target_weight: float
    delta: float


class OptimizeResultOut(BaseModel):
    """spec §5 P2-6 OptimizeResult。"""

    portfolio_id: int
    as_of: str | None = None
    is_delayed: bool
    objective: str
    cash_weight: float  # 0..1
    weights: list[OptimizeWeightOut]
    expected_annual_return: float | None = None
    expected_annual_volatility: float | None = None
    expected_sharpe: float | None = None
    constraints_applied: dict[str, Any]
    infeasible: bool


class BacktestCurvePointOut(BaseModel):
    """spec §4.4 backtest 累積曲線の 1 点（value は 1 始まりの倍率）。"""

    date: str
    value: float


class BacktestLegOut(BaseModel):
    """spec §4.4 backtest の 1 系列（ポート/ベンチ共通形）。"""

    cumulative_return: float
    annual_return: float
    sharpe: float | None = None
    max_drawdown: float
    curve: list[BacktestCurvePointOut]


class BacktestResultOut(BaseModel):
    """spec §4.4 backtest 結果（現保有 buy&hold vs 指数）。"""

    portfolio_id: int
    as_of: str | None = None
    is_delayed: bool
    portfolio: BacktestLegOut
    benchmark: BacktestLegOut
    excess_return: float


# ---------------------------------------------------------------------------
# ヘルパ: 既定ポートフォリオ解決と holdings 構築
# ---------------------------------------------------------------------------


def _resolve_portfolio(conn: Connection, portfolio_id: int | None = None) -> int:
    """portfolio_id が省略された場合は GET /portfolios の先頭で解決する（裁定 L-9）。"""
    if portfolio_id is not None:
        return portfolio_id
    rows = repo.list_portfolios(conn)
    if not rows:
        raise HTTPException(status_code=404, detail="ポートフォリオが存在しません。")
    return int(rows[0]["portfolio_id"])


def _build_holdings_response(conn: Connection, portfolio_id: int) -> HoldingsResponse:
    """holdings を評価額付きで構築して HoldingsResponse にする。

    DB から holdings + latest_closes を引き、value_holdings で評価額を付与する。
    as_of は daily_quotes の MAX(date)（Free 12週遅延）。
    """
    holdings_rows = repo.list_holdings(conn, portfolio_id)
    codes = [h["code"] for h in holdings_rows]
    latest_closes = repo.get_latest_closes(conn, codes) if codes else {}
    valued = value_holdings(holdings_rows, latest_closes)

    as_of = repo.get_max_daily_date(conn)

    return HoldingsResponse(
        portfolio_id=portfolio_id,
        holdings=[HoldingOut(**h) for h in valued],
        valuation_meta=ValuationMeta(
            as_of=as_of,
            is_delayed=True,  # Free 12週遅延（ADR-008）
            plan="free",
        ),
    )


# ---------------------------------------------------------------------------
# エンドポイント
# ---------------------------------------------------------------------------


@router.get("/portfolios", response_model=list[PortfolioOut])
def list_portfolios(
    conn: Connection = Depends(get_conn),
) -> list[PortfolioOut]:
    """ポートフォリオ一覧を返す（spec P2-1）。先頭が既定ポートフォリオ（裁定 L-9）。"""
    rows = repo.list_portfolios(conn)
    return [PortfolioOut(**r) for r in rows]


@router.get("/holdings", response_model=HoldingsResponse)
def get_holdings(
    portfolio_id: int | None = Query(default=None, description="省略時は先頭ポートフォリオ"),
    conn: Connection = Depends(get_conn),
) -> HoldingsResponse:
    """保有銘柄と評価額を返す（spec P2-2）。

    last_close / market_value / unrealized_pnl は Free 12週遅延株価（ADR-008）。
    valuation_meta に as_of（日付）と is_delayed=True を付与する。
    """
    pid = _resolve_portfolio(conn, portfolio_id)
    return _build_holdings_response(conn, pid)


@router.post("/transactions", response_model=TransactionResult, status_code=201)
def post_transaction(
    body: TransactionIn,
) -> TransactionResult:
    """取引を記録し holdings を再計算して返す（spec P2-2・ADR-019）。

    1. transactions に INSERT。
    2. recalc_holdings で holdings を入れ替え（ADR-019）。
    3. 更新後 holdings を評価額付きで返す。
    1〜3 は同じトランザクション内で行い、中間状態を残さない。
    """
    row: dict[str, Any] = {
        "portfolio_id": body.portfolio_id,
        "code": body.code,
        "side": body.side,
        "shares": body.shares,
        "price": body.price,
        "traded_at": body.traded_at,
    }
    if body.fee is not None:
        row["fee"] = body.fee

    with get_engine().begin() as conn:
        # portfolio 存在確認
        _resolve_portfolio(conn, body.portfolio_id)

        # transactions と holdings 再導出を atomic にする（ADR-019）。
        txn_id = repo.insert_transaction(conn, row)
        recalc_holdings(conn, body.portfolio_id)
        holdings_resp = _build_holdings_response(conn, body.portfolio_id)
    return TransactionResult(transaction_id=txn_id, holdings=holdings_resp)


@router.get("/transactions", response_model=list[TransactionOut])
def list_transactions_endpoint(
    portfolio_id: int | None = Query(default=None, description="省略時は先頭ポートフォリオ"),
    conn: Connection = Depends(get_conn),
) -> list[TransactionOut]:
    """取引履歴を新しい順で返す（spec P2-2・ADR-019）。

    company_name は stocks と突き合わせて補完する（repo の JOIN 流儀に倣い router で付与）。
    list_transactions は holdings 再導出用に昇順だが、履歴表示は新しい順
    （traded_at 降順・同日は id 降順）で返す。
    """
    pid = _resolve_portfolio(conn, portfolio_id)
    txns = repo.list_transactions(conn, pid)

    # code -> company_name の対応を引く（stocks から該当コード分だけ拾う）
    names = {s["code"]: s.get("company_name") for s in repo.list_stocks(conn)}

    # 新しい順（traded_at 降順・同日は id 降順）に並べ替える
    txns_sorted = sorted(txns, key=lambda t: (t["traded_at"], t["id"]), reverse=True)

    return [
        TransactionOut(
            id=t["id"],
            code=t["code"],
            company_name=names.get(t["code"]),
            side=t["side"],
            shares=t["shares"],
            price=t["price"],
            fee=t.get("fee"),
            traded_at=t["traded_at"],
        )
        for t in txns_sorted
    ]


@router.put("/transactions/{txn_id}", response_model=TransactionResult)
def put_transaction(
    txn_id: int,
    body: TransactionIn,
) -> TransactionResult:
    """取引を更新し holdings を再計算して返す（spec P2-2・ADR-019）。

    1. get_transaction で存在確認（無ければ 404）。
    2. transactions を UPDATE。
    3. recalc_holdings で holdings を入れ替え（ADR-019）。
    4. 更新後 holdings を評価額付きで返す。
    2〜4 は同じトランザクション内で行い、中間状態を残さない。
    """
    row: dict[str, Any] = {
        "code": body.code,
        "side": body.side,
        "shares": body.shares,
        "price": body.price,
        "fee": body.fee,
        "traded_at": body.traded_at,
    }

    with get_engine().begin() as conn:
        existing = repo.get_transaction(conn, txn_id)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"取引 {txn_id} は存在しません。")
        # recalc は取引の**実所属** portfolio で行う（body.portfolio_id ではない・#26）。
        # update_transaction は portfolio_id を更新しないので取引は元の portfolio に留まる。body の
        # 別 portfolio_id で recalc すると、その portfolio を誤再計算しつつ実所属側の holdings を
        # 取引と乖離させる（delete_transaction_endpoint と同型＝実所属で recalc に揃える）。
        pid = int(existing["portfolio_id"])

        # 取引更新と holdings 再導出を atomic にする（ADR-019）。
        repo.update_transaction(conn, txn_id, row)
        recalc_holdings(conn, pid)
        holdings_resp = _build_holdings_response(conn, pid)
    return TransactionResult(transaction_id=txn_id, holdings=holdings_resp)


@router.delete("/transactions/{txn_id}", response_model=TransactionResult)
def delete_transaction_endpoint(
    txn_id: int,
) -> TransactionResult:
    """取引を削除し holdings を再計算して返す（spec P2-2・ADR-019）。

    1. get_transaction で存在確認＆所属 portfolio_id を取得（無ければ 404）。
    2. transactions を DELETE。
    3. recalc_holdings で holdings を入れ替え（ADR-019）。
    4. 更新後 holdings を評価額付きで返す。
    2〜4 は同じトランザクション内で行い、中間状態を残さない。
    """
    with get_engine().begin() as conn:
        existing = repo.get_transaction(conn, txn_id)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"取引 {txn_id} は存在しません。")
        pid = int(existing["portfolio_id"])

        # 取引削除と holdings 再導出を atomic にする（ADR-019）。
        repo.delete_transaction(conn, txn_id)
        recalc_holdings(conn, pid)
        holdings_resp = _build_holdings_response(conn, pid)
    return TransactionResult(transaction_id=txn_id, holdings=holdings_resp)


@router.get("/portfolio/{portfolio_id}/metrics", response_model=PortfolioMetricsOut)
def get_portfolio_metrics(
    portfolio_id: int,
    conn: Connection = Depends(get_conn),
) -> PortfolioMetricsOut:
    """保有ポートフォリオの相関・シャープ・MDD・policy 逸脱を返す（spec P2-5）。

    compute_portfolio_metrics（quant）に price_panel・現ウェイト・policy・labels を渡す。
    保有 1 銘柄・履歴不足は quant が null/空を返すのでそのまま通す（ADR-014）。
    """
    # portfolio 存在確認
    rows = repo.list_portfolios(conn)
    if not any(r["portfolio_id"] == portfolio_id for r in rows):
        raise HTTPException(
            status_code=404, detail=f"ポートフォリオ {portfolio_id} は存在しません。"
        )

    holdings_rows = repo.list_holdings(conn, portfolio_id)
    codes = [h["code"] for h in holdings_rows]

    price_panel = build_price_panel(conn, codes)
    latest_closes = repo.get_latest_closes(conn, codes) if codes else {}
    valued = value_holdings(holdings_rows, latest_closes)
    weights = current_stock_weights(valued)

    labels = {h["code"]: h.get("company_name") or h["code"] for h in holdings_rows}
    policy = get_policy(conn)

    result = compute_portfolio_metrics(price_panel, weights, policy, labels)

    # deviations は asset-overview と同値にするため、現金・外部資産まで含む完全な文脈で
    # 計算する共有ヘルパで上書きする（決定6・B-12: 計算 1 か所・出力先 2 つ）。
    # compute_portfolio_metrics 内部の deviations は現金文脈を持たないため採用しない。
    deviations = portfolio_deviations(conn, portfolio_id)

    corr_raw = result.get("correlation") or {"codes": [], "labels": [], "matrix": []}
    return PortfolioMetricsOut(
        portfolio_id=portfolio_id,
        as_of=result.get("as_of"),
        is_delayed=bool(result.get("is_delayed", True)),
        annual_return=result.get("annual_return"),
        annual_volatility=result.get("annual_volatility"),
        sharpe=result.get("sharpe"),
        max_drawdown=result.get("max_drawdown"),
        lookback_days=result.get("lookback_days"),
        correlation=CorrelationMatrixOut(**corr_raw),
        deviations=[DeviationOut(**d) for d in deviations],
    )


@router.post("/portfolio/{portfolio_id}/optimize", response_model=OptimizeResultOut)
def post_optimize(
    portfolio_id: int,
    body: OptimizeIn | None = None,
    conn: Connection = Depends(get_conn),
) -> OptimizeResultOut:
    """policy 制約付き平均分散最適化を実行して返す（spec P2-6）。

    body は省略可（省略時は policy そのまま）。送った場合は policy に上書きをマージ。
    infeasible は 422 にせず infeasible=True のまま 200 で返す（spec §5 P2-6）。
    company_name は holdings JOIN stocks で付与（ADR-014: quant は DB を知らない）。
    """
    # portfolio 存在確認
    rows = repo.list_portfolios(conn)
    if not any(r["portfolio_id"] == portfolio_id for r in rows):
        raise HTTPException(
            status_code=404, detail=f"ポートフォリオ {portfolio_id} は存在しません。"
        )

    holdings_rows = repo.list_holdings(conn, portfolio_id)
    codes = [h["code"] for h in holdings_rows]

    price_panel = build_price_panel(conn, codes)
    latest_closes = repo.get_latest_closes(conn, codes) if codes else {}
    valued = value_holdings(holdings_rows, latest_closes)
    weights = current_stock_weights(valued)

    # policy に body の上書きをマージ
    policy = get_policy(conn)
    if body is not None:
        if body.target_cash_ratio is not None:
            policy["target_cash_ratio"] = body.target_cash_ratio
        if body.max_position_weight is not None:
            policy["max_position_weight"] = body.max_position_weight
        if body.sector_caps is not None:
            policy["sector_caps"] = body.sector_caps

    # sectors: code -> sector33_code（holdings.sector33_code が入っている）
    sectors = {h["code"]: h.get("sector33_code") or "" for h in holdings_rows}

    result = optimize_portfolio(
        price_panel=price_panel,
        policy=policy,
        sectors=sectors,
        objective="max_sharpe",
        current_weights=weights if weights else None,
    )

    # company_name を weight 行に JOIN で付与（quant は DB を知らない＝ADR-014）
    code_to_name = {h["code"]: h.get("company_name") for h in holdings_rows}
    weights_out = [
        OptimizeWeightOut(
            code=w["code"],
            company_name=code_to_name.get(w["code"]),
            current_weight=w.get("current_weight"),
            target_weight=float(w["target_weight"]),
            delta=float(w["delta"]),
        )
        for w in result.get("weights", [])
    ]

    return OptimizeResultOut(
        portfolio_id=portfolio_id,
        as_of=result.get("as_of"),
        is_delayed=bool(result.get("is_delayed", True)),
        objective=result.get("objective", "max_sharpe"),
        cash_weight=float(result.get("cash_weight", 0.0)),
        weights=weights_out,
        expected_annual_return=result.get("expected_annual_return"),
        expected_annual_volatility=result.get("expected_annual_volatility"),
        expected_sharpe=result.get("expected_sharpe"),
        constraints_applied=result.get("constraints_applied", {}),
        infeasible=bool(result.get("infeasible", False)),
    )


@router.get("/portfolio/{portfolio_id}/backtest", response_model=BacktestResultOut)
def get_portfolio_backtest(
    portfolio_id: int,
    conn: Connection = Depends(get_conn),
) -> BacktestResultOut:
    """現保有の buy&hold バックテストを対指数（TOPIX）で返す（spec §4.4・§8）。

    backtest_portfolio_service（service→quant 純関数）に委譲する。保有 0・履歴不足・
    benchmark 未取得は純関数が空 leg（as_of=None / curve=[]）を返すのでそのまま通す
    （エラーにしない＝ADR-014）。
    """
    # portfolio 存在確認（metrics/optimize と同じ）
    rows = repo.list_portfolios(conn)
    if not any(r["portfolio_id"] == portfolio_id for r in rows):
        raise HTTPException(
            status_code=404, detail=f"ポートフォリオ {portfolio_id} は存在しません。"
        )

    result = backtest_portfolio_service(conn, portfolio_id)
    return BacktestResultOut(
        portfolio_id=portfolio_id,
        as_of=result.get("as_of"),
        is_delayed=bool(result.get("is_delayed", True)),
        portfolio=BacktestLegOut(**result["portfolio"]),
        benchmark=BacktestLegOut(**result["benchmark"]),
        excess_return=float(result.get("excess_return", 0.0)),
    )
