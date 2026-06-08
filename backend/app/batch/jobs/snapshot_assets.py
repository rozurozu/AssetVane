"""夜間バッチ: 資産スナップショット焼きジョブ（phase2-spec.md §3.3・ADR-054）。

保有評価額（最新 daily_quotes）＋ 現金 ＋ 外部資産 ＋ 投信評価額（最新 NAV）を集計し、
asset_snapshots テーブルに 1 日 1 行 UPSERT する。
評価額の計算は services/portfolio・services/fund_holdings の関数を使う
（ADR-014: 計算は Python が担う）。
例外はジョブ境界で握り JobResult(ok=False) で返す（runner.py の仕様に合わせる）。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.batch.runner import JobResult
from app.db import repo
from app.db.engine import get_engine
from app.services.fund_holdings import value_fund_holdings
from app.services.portfolio import value_holdings

logger = logging.getLogger(__name__)


def run() -> JobResult:
    """資産スナップショットを today 日付で 1 行 UPSERT する。

    1. 先頭ポートフォリオの holdings × 最新 close で株式評価額を集計。
    2. cash テーブルの残高（未登録は 0）。
    3. external_assets の value 合計（未登録は 0）。
    4. fund_holdings × 最新 NAV で投信評価額を集計（10,000 口換算・ADR-054）。
    5. asset_snapshots に today の行を UPSERT（再実行しても冪等）。
    """
    today = datetime.now(UTC).strftime("%Y-%m-%d")

    try:
        with get_engine().connect() as conn:
            # --- 先頭ポートフォリオ ---
            portfolios = repo.list_portfolios(conn)
            portfolio_id: int | None = portfolios[0]["portfolio_id"] if portfolios else None

            # --- 株式評価額の集計 ---
            stock_value = 0.0
            pnl = 0.0
            if portfolio_id is not None:
                holdings_rows = repo.list_holdings(conn, portfolio_id)
                codes = [h["code"] for h in holdings_rows]
                if codes:
                    latest_closes = repo.get_latest_closes(conn, codes)
                    valued = value_holdings(holdings_rows, latest_closes)
                    for h in valued:
                        if h.get("market_value") is not None:
                            stock_value += float(h["market_value"])
                        if h.get("unrealized_pnl") is not None:
                            pnl += float(h["unrealized_pnl"])

            # --- 現金 ---
            cash_row = repo.get_cash(conn)
            cash_value = float(cash_row["balance"]) if cash_row else 0.0

            # --- 外部資産 ---
            ext_rows = repo.list_external_assets(conn)
            external_value = sum(float(r["value"]) for r in ext_rows if r.get("value") is not None)

            # --- 投信評価額（最新 NAV・10,000 口換算・ADR-054） ---
            fund_value = 0.0
            if portfolio_id is not None:
                fund_rows = repo.list_fund_holdings(conn, portfolio_id)
                isins = [f["isin"] for f in fund_rows]
                if isins:
                    latest_navs = repo.get_latest_fund_navs(conn, isins)
                    for f in value_fund_holdings(fund_rows, latest_navs):
                        if f.get("market_value") is not None:
                            fund_value += float(f["market_value"])
                        if f.get("unrealized_pnl") is not None:
                            pnl += float(f["unrealized_pnl"])

        total_value = stock_value + cash_value + external_value + fund_value

        rows = [
            {
                "date": today,
                "total_value": total_value,
                "stock_value": stock_value,
                "cash_value": cash_value,
                "external_value": external_value,
                "fund_value": fund_value,
                "pnl": pnl,
            }
        ]
        upserted = repo.upsert_asset_snapshots(rows)
        logger.info(
            "snapshot_assets: %s total=%.0f stock=%.0f cash=%.0f ext=%.0f fund=%.0f "
            "pnl=%.0f (upserted=%d)",
            today,
            total_value,
            stock_value,
            cash_value,
            external_value,
            fund_value,
            pnl,
            upserted,
        )
        return JobResult(
            name="snapshot_assets",
            ok=True,
            rows=upserted,
            detail=f"total={total_value:.0f}",
        )

    except Exception as exc:  # noqa: BLE001 — ジョブ境界で握り runner に返す
        logger.exception("snapshot_assets: 失敗")
        return JobResult(name="snapshot_assets", ok=False, rows=0, detail=str(exc))
