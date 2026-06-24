"""銘柄マスタ同期ジョブ — 全銘柄マスタを同期する（spec §3.6・裁定 L-5）。

まず `fetch_master_all()`（`code` 無しの一括取得）を試し、結果があれば `stocks` を UPSERT。
空/失敗ならフォールバック: `daily_quotes` に存在して `stocks` に無い code を集め、
`fetch_master(missing_codes)` で後追い補完する（新規分だけの少 req）。
`fetch_master_all` が使えない場合でも、日足を取った全 code を種に追従できる。

`is_etf` 是正は Phase 1 では行わない（docs どおり Phase 7 温存・裁定 L-4）。全銘柄取得で
ETF/REIT 行が is_etf=0 で混ざるが、market_code（Mkt）で実質判別できる（jquants.py のコメント参照）。
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from app.batch.runner import JobResult
from app.db import repo
from app.db.engine import get_engine
from app.db.schema import daily_quotes, stocks
from app.services.jquants_config import build_jquants_adapter

logger = logging.getLogger(__name__)


def _missing_codes() -> list[str]:
    """daily_quotes に存在するが stocks に無い code を集める（フォールバック補完用）。"""
    with get_engine().connect() as conn:
        quote_codes = set(conn.execute(select(daily_quotes.c.code).distinct()).scalars().all())
        known = set(conn.execute(select(stocks.c.code)).scalars().all())
    return sorted(quote_codes - known)


def run() -> JobResult:
    """全銘柄マスタを同期する（spec §3.6）。

    例外（JQuantsError 等）は握って JobResult(ok=False) で返す（runner が Discord 通知）。
    """
    try:
        adapter = build_jquants_adapter()

        # まず全件一括取得を試す（1〜数 req）。
        all_rows = adapter.fetch_master_all()
        all_rows = [r for r in all_rows if r.get("code")]
        if all_rows:
            n = repo.upsert_stocks(all_rows)
            return JobResult(
                name="sync_master",
                ok=True,
                rows=n,
                detail=f"fetch_master_all で {n} 行 UPSERT",
            )

        # フォールバック: 日足の code を種に、stocks に無い分だけ後追い補完。
        missing = _missing_codes()
        if not missing:
            return JobResult(
                name="sync_master",
                ok=True,
                rows=0,
                detail="fetch_master_all は空・補完対象 code なし（同期不要）",
            )
        rows = adapter.fetch_master(missing)
        rows = [r for r in rows if r.get("code")]
        n = repo.upsert_stocks(rows)
        return JobResult(
            name="sync_master",
            ok=True,
            rows=n,
            detail=f"fetch_master_all 空 → 不足 {len(missing)} 件を後追い補完・{n} 行 UPSERT",
        )
    except Exception as exc:  # noqa: BLE001 — ジョブ境界で握り runner に返す
        logger.exception("sync_master が失敗")
        return JobResult(name="sync_master", ok=False, rows=0, detail=f"失敗: {exc}")
