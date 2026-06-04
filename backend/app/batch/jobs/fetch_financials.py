"""財務取得ジョブ — 保有銘柄の財務データを取得し financials に UPSERT する（spec §3.2）。

保有銘柄（holdings の code）に限定して
`JQuantsAdapter.fetch_financials(code=...)` をループ取得する。
差分は `fetch_meta['financials']` の last_fetched_date（開示日ベース）で管理する（ADR-018）。
保有が無ければ 0 行で ok を返す。
例外はジョブ境界で握り `JobResult(ok=False)` で返す（fetch_quotes.py の構造を踏襲）。
書き込みは UPSERT で冪等（ADR-002）。

[注意] V2 財務エンドポイントと実フィールド名は未確定（jquants.md §6 要再確認）。
"""

from __future__ import annotations

import logging
from datetime import date

from app.adapters.jquants import JQuantsAdapter
from app.batch.runner import JobResult
from app.db import repo
from app.db.engine import get_engine

logger = logging.getLogger(__name__)

_SOURCE = "financials"
_DEFAULT_PORTFOLIO_ID = 1  # 単一ポートフォリオ前提（ADR-001・spec §1）


def run() -> JobResult:
    """保有銘柄の財務データを取得し financials / fetch_meta を前進させる（spec §3.2）。

    1. holdings から保有銘柄コードを取得する。
    2. 各銘柄に対して JQuantsAdapter.fetch_financials(code=...) を呼ぶ。
    3. financials に UPSERT し、fetch_meta['financials'] を今日の日付まで前進させる。
    保有が無ければ 0 行で ok を返す（正常終了）。
    例外（JQuantsError 含む）はジョブ境界で握り runner に返す（ADR-018）。
    """
    total_rows = 0
    failed_codes: list[str] = []

    try:
        with get_engine().connect() as conn:
            codes = repo.list_holding_codes(conn, _DEFAULT_PORTFOLIO_ID)

        if not codes:
            logger.info("fetch_financials: 保有銘柄が 0 件のためスキップ。")
            return JobResult(
                name="fetch_financials",
                ok=True,
                rows=0,
                detail="保有銘柄が 0 件のためスキップ",
            )

        adapter = JQuantsAdapter()

        for code in codes:
            try:
                rows = adapter.fetch_financials(code=code)
                # code/disclosed_date/fiscal_period が欠けた行は弾く（PK NULL を防ぐ）
                rows = [
                    r
                    for r in rows
                    if r.get("code") and r.get("disclosed_date") and r.get("fiscal_period")
                ]
                if rows:
                    total_rows += repo.upsert_financials(rows)
                logger.info("fetch_financials: %s・%d 行 UPSERT", code, len(rows))
            except Exception as exc:  # noqa: BLE001 — 銘柄単位で握り後続を継続
                logger.exception("fetch_financials: %s が失敗", code)
                failed_codes.append(f"{code}: {exc}")

        # fetch_meta を前進させる（開示日ベース・今日の日付で記録）
        today = date.today().isoformat()
        repo.upsert_fetch_meta(_SOURCE, today)

    except Exception as exc:  # noqa: BLE001 — ジョブ境界（JQuantsError 等）で握り runner に返す
        logger.exception("fetch_financials が失敗")
        return JobResult(
            name="fetch_financials",
            ok=False,
            rows=total_rows,
            detail=f"未捕捉例外: {exc}",
        )

    if failed_codes:
        detail = f"失敗銘柄: {', '.join(failed_codes)}"
        return JobResult(
            name="fetch_financials",
            ok=False,
            rows=total_rows,
            detail=detail,
        )

    return JobResult(
        name="fetch_financials",
        ok=True,
        rows=total_rows,
        detail=f"保有 {len(codes)} 銘柄・{total_rows} 行 UPSERT",
    )
