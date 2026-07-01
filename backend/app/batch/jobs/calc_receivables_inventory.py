"""売掛/在庫の質ジョブ（JP）— watchlist/holdings の DSO/DIO・受取債権/在庫 YoY を焼く（
ADR-064 #2）。

edinetdb.jp の構造化財務（trade_receivables/inventories/revenue/gross_profit）を銘柄コード直引きで
取り、
services.edinetdb_quality が quant.valuation に畳んで valuation_snapshots の #2 列を UPDATE する
（計算は Python・解釈は LLM＝ADR-014/016）。calc_valuation が as_of_date 込みで焼いた行を前提に
**既存行を UPDATE**（NIGHTLY 順で calc_valuation の後）。

レート予算（無料枠 日100/月600）を守るため:
- 対象は watchlist ∪ 全保有（低頻度・銘柄単位）。
- 各銘柄は edinetdb_refresh_interval_days（既定 7 日）あけて再取得（fetch_meta で per-code 追跡＝
  財務は四半期更新ゆえ週次で十分）。
- 1 晩の処理本数は plan の nightly_soft_cap で天井。
月残予算が edinetdb_monthly_reserve を切ったら打切。
- 未設定（edinetdb_config 未登録）は静かに skip（ok=True＝公式 EDINET の必須扱いと違う・ADR-064）。

個別銘柄の失敗は握って後続継続（ADR-018）。ジョブ全体の例外は JobResult(ok=False) で runner に返す。
"""

from __future__ import annotations

import logging
from datetime import date

from app.adapters.edinetdb import EdinetDbAdapterError
from app.batch import state
from app.batch.runner import JobResult
from app.config import settings
from app.db import repo
from app.db.engine import get_engine
from app.services import edinetdb_quality
from app.services.edinetdb_config import (
    build_edinetdb_adapter,
    current_plan,
    plan_limits,
    resolve_edinetdb_config,
)

logger = logging.getLogger(__name__)

_NAME = "calc_receivables_inventory"
_META_PREFIX = "edinetdb_quality:"


def _recently_fetched(conn, code: str, today: str, interval_days: int) -> bool:
    """この code の #2 を interval_days 以内に取得済みか（cadence・fetch_meta 追跡）。"""
    meta = repo.get_fetch_meta(conn, f"{_META_PREFIX}{code}")
    last = (meta or {}).get("last_fetched_date")
    if not last:
        return False
    try:
        gap = (date.fromisoformat(today) - date.fromisoformat(last)).days
    except ValueError:
        return False
    return gap < interval_days


def run() -> JobResult:
    """watchlist/holdings の売掛/在庫の質を edinetdb.jp から焼く（cadence＋予算ガード・
    ADR-064 #2）。"""
    try:
        with get_engine().connect() as conn:
            if resolve_edinetdb_config(conn) is None:
                return JobResult(
                    name=_NAME, ok=True, rows=0, detail="EDINET DB 未設定のため skip（ADR-064）"
                )
            plan = current_plan(conn)
            adapter = build_edinetdb_adapter(conn)
            targets = sorted(
                set(repo.list_all_holding_codes(conn))
                | {w["code"] for w in repo.list_watchlist(conn)}
            )
            edinet_map = repo.get_stock_edinet_codes(conn, targets)

        limits = plan_limits(plan)
        reserve = settings.edinetdb_monthly_reserve
        interval_days = settings.edinetdb_refresh_interval_days
        today = date.today().isoformat()

        processed = 0  # 当夜の API リクエスト消費（soft_cap で天井）
        updated = 0  # valuation_snapshots を実際に更新できた件数
        skipped_cadence = 0

        # cadence＋予算ガードで元々短命だが、停止も最内ループで見る（stop_aware・ADR-036/070）。
        for code in state.stop_aware(targets):
            if processed >= limits.nightly_soft_cap:
                break
            mo_rem = adapter.last_budget.get("monthly_remaining")
            if mo_rem is not None and mo_rem <= reserve:
                logger.warning("edinetdb 月残予算 %s が予備 %s 以下のため打切", mo_rem, reserve)
                break
            with get_engine().connect() as conn:
                if _recently_fetched(conn, code, today, interval_days):
                    skipped_cadence += 1
                    continue
            try:
                edinet_code = edinet_map.get(code)
                if not edinet_code:
                    edinet_code = adapter.resolve_edinet_code(code)
                    processed += 1
                    with get_engine().begin() as conn:
                        repo.set_stock_edinet_code(conn, code, edinet_code)
                if not edinet_code:
                    # edinetdb.jp に未収載＝解決済みとして cadence を進め毎晩の空振りを防ぐ。
                    repo.upsert_fetch_meta(f"{_META_PREFIX}{code}", today)
                    continue
                fins = adapter.get_financials(edinet_code)
                processed += 1
                quality = edinetdb_quality.compute_quality_from_financials(fins)
                if quality:
                    with get_engine().begin() as conn:
                        if repo.update_valuation_receivables_inventory(conn, code, quality):
                            updated += 1
                repo.upsert_fetch_meta(f"{_META_PREFIX}{code}", today)
            except EdinetDbAdapterError as exc:
                logger.warning("calc_receivables_inventory code=%s 取得失敗: %s", code, exc)
                continue

        budget = adapter.last_budget
        mo = budget.get("monthly_remaining")
        budget_note = f"・月残 {mo}" if mo is not None else ""
        return JobResult(
            name=_NAME,
            ok=True,
            rows=updated,
            detail=(
                f"#2 売掛/在庫 {updated} 件更新"
                f"（API {processed} 回・cadence skip {skipped_cadence}{budget_note}）"
            ),
        )
    except Exception as exc:  # noqa: BLE001 — ジョブ境界で握り runner に返す（ADR-018）
        logger.exception("calc_receivables_inventory が失敗")
        return JobResult(name=_NAME, ok=False, rows=0, detail=f"失敗: {exc}")
