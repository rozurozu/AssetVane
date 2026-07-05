"""売掛/在庫の質＋清原式ネットキャッシュのジョブ（JP）— 全上場普通株に焼く（ADR-064 #2・ADR-079・
ADR-083）。

edinetdb.jp の構造化財務（trade_receivables/inventories/revenue/gross_profit＋流動資産/投資有価証券/
総負債）を銘柄コード直引きで取り、services.edinetdb_quality が quant.valuation に畳んで
valuation_snapshots の #2 列＋net_cash を UPDATE する（計算は Python・解釈は LLM＝ADR-014/016）。
calc_valuation が as_of_date 込みで焼いた行を前提に**既存行を UPDATE**（NIGHTLY 順で calc_valuation
の後）。

**母集団は JP 普通株の全ユニバース**（ADR-083＝清原式ネットキャッシュ発掘には全銘柄が要る。当初は
watchlist∪holdings 限定だった＝ADR-079 の相乗り設計を上書き）。全銘柄を毎晩叩くとレート予算（free
日100/月600・pro 日1000/月10000）が尽きるため、対象を賢く絞る（`_select_targets`）:
- **定常（full_backfill=False・夜間）**: 初回（fetch_meta 無し）か **新規開示**（財務の最新
  disclosed_date が前回焼き日より新しい）銘柄だけ焼く（＝更新があった銘柄だけ・予算節約）。
- **初回一括（full_backfill=True・`POST /valuation/backfill-net-cash` ボタン）**: net_cash 未焼き
  （NULL）を cadence 外で優先的に焼く。ソフトキャップを日次予算まで引き上げて数日で全銘柄を埋める。

予算ガード（両モード共通）: 月残予算が edinetdb_monthly_reserve を切ったら打切・停止は最内ループでも
見る（stop_aware・ADR-036/070）。未設定（edinetdb_config 未登録）は静かに skip（ok=True・ADR-064）。
個別銘柄の失敗は握って後続継続（ADR-018）。ジョブ全体の例外は JobResult(ok=False) で runner に返す。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
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


def _within_cadence(today: str, last: str | None, interval_days: int) -> bool:
    """この銘柄を interval_days 以内に取得済みか（cadence・辞書引き版・ADR-064/083）。

    last が None（未取得）は「cadence 内でない」＝False（初回として焼ける）。日付が壊れていても
    False に倒す（＝焼く側に倒して取りこぼさない）。
    """
    if not last:
        return False
    try:
        gap = (date.fromisoformat(today) - date.fromisoformat(last)).days
    except ValueError:
        return False
    return gap < interval_days


def _select_targets(
    codes: Iterable[str],
    *,
    full_backfill: bool,
    net_cash_missing: set[str],
    last_fetched: dict[str, str],
    disclosed: dict[str, str],
    today: str,
    interval_days: int,
) -> list[str]:
    """焼き込み対象の銘柄を選ぶ純関数（DB を知らない・ADR-083/016）。

    - **full_backfill（初回一括・ボタン）**: net_cash 未焼き（NULL）を cadence 外で焼く（一巡で
      NULL が消えて対象ゼロ＝冪等・IFRS 等の恒久 NULL は interval_days で連打を防ぐ）。
    - **定常（夜間）**: 初回（fetch_meta 無し）か 新規開示（disclosed_date > 前回焼き日）だけ焼く
      （開示が無ければ叩かない＝予算節約・「更新があった銘柄だけ」を正確に表す）。
    disclosed/last_fetched は 'YYYY-MM-DD' で文字列比較＝時系列順。
    """
    out: list[str] = []
    for code in codes:
        lf = last_fetched.get(code)
        if full_backfill:
            if code in net_cash_missing and not _within_cadence(today, lf, interval_days):
                out.append(code)
        elif lf is None:
            out.append(code)  # 初回（一度も焼いていない）
        else:
            dd = disclosed.get(code)
            if dd is not None and dd > lf:
                out.append(code)  # 新規開示（前回焼き日より新しい決算が出た）
    return out


def run(full_backfill: bool = False) -> JobResult:
    """JP 普通株の売掛/在庫の質＋清原式 net_cash を edinetdb.jp から焼く（ADR-064 #2・ADR-079・
    ADR-083）。full_backfill=True は net_cash 未焼きを日次予算まで一気に焼く初回モード。"""
    try:
        with get_engine().connect() as conn:
            if resolve_edinetdb_config(conn) is None:
                return JobResult(
                    name=_NAME, ok=True, rows=0, detail="EDINET DB 未設定のため skip（ADR-064）"
                )
            plan = current_plan(conn)
            adapter = build_edinetdb_adapter(conn)
            universe = sorted(repo.list_jp_universe_codes(conn))
            edinet_map = repo.get_stock_edinet_codes(conn, universe)
            # 選別の下ごしらえ（全銘柄ぶんを 1 クエリずつ・ループ内 N クエリを避ける・ADR-016）。
            net_cash_missing = repo.list_codes_missing_net_cash(conn)
            last_fetched = repo.get_fetch_metas_by_prefix(conn, _META_PREFIX)
            disclosed = repo.get_max_disclosed_date_by_code(conn)

        limits = plan_limits(plan)
        reserve = settings.edinetdb_monthly_reserve
        interval_days = settings.edinetdb_refresh_interval_days
        today = date.today().isoformat()
        # full_backfill は初回を速く進めるためソフトキャップを日次予算まで引き上げる（月残ガード＋
        # ヘッダ x-ratelimit が実 enforce・ADR-083）。定常は月予算に予備を残す nightly_soft_cap。
        soft_cap = limits.daily_budget if full_backfill else limits.nightly_soft_cap

        targets = _select_targets(
            universe,
            full_backfill=full_backfill,
            net_cash_missing=net_cash_missing,
            last_fetched=last_fetched,
            disclosed=disclosed,
            today=today,
            interval_days=interval_days,
        )

        processed = 0  # 当夜の API リクエスト消費（soft_cap で天井）
        updated = 0  # valuation_snapshots を実際に更新できた件数

        # cadence で選別済みだが、停止も最内ループで見る（stop_aware・ADR-036/070）。
        for code in state.stop_aware(targets):
            if processed >= soft_cap:
                break
            mo_rem = adapter.last_budget.get("monthly_remaining")
            if mo_rem is not None and mo_rem <= reserve:
                logger.warning("edinetdb 月残予算 %s が予備 %s 以下のため打切", mo_rem, reserve)
                break
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
        mode = "初回一括" if full_backfill else "差分"
        return JobResult(
            name=_NAME,
            ok=True,
            rows=updated,
            detail=(
                f"#2＋net_cash {updated} 件更新"
                f"（{mode}・対象 {len(targets)} 件・API {processed} 回{budget_note}）"
            ),
        )
    except Exception as exc:  # noqa: BLE001 — ジョブ境界で握り runner に返す（ADR-018）
        logger.exception("calc_receivables_inventory が失敗")
        return JobResult(name=_NAME, ok=False, rows=0, detail=f"失敗: {exc}")
