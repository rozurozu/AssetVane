"""FX レート（USDJPY）取得ジョブ — FxAdapter で日足終値を取り fx_rates に焼く（Phase 7(B-2)）。

ADR-010/057（FX アダプタ・FX/保有波及）。ADR-002（UPSERT 冪等）。ADR-018（部分失敗の握り）。
fetch_us_quotes.py の差分カーソル前進の作法をミラーした FX 版。

差分取得: `fetch_meta['fx:USDJPY']` の `last_fetched_date` の翌日〜today を FxAdapter().fetch_rates
で取り、`repo.upsert_fx_rates(rows)` して取得最大日で fetch_meta を前進させる。
fetch_meta 不在/NULL なら初期窓（今日 − backfill_years 年）から取得する（fetch_us_quotes 同型）。

0 行でも ok=True（休場日＝差分なしは正常）。
取得に失敗した場合は ok=False を返す（snapshot_assets が当夜の FX を読めないことを runner が通知）。

NIGHTLY_JOBS での配置: snapshot_assets の**直前**（fetch_fund_navs の隣）に置くこと。
理由: snapshot_assets が当夜の FX レートを `get_latest_fx_rate` で参照するため、評価額を焼く前に
FX を揃える必要がある（fetch_fund_navs が snapshot 前に NAV を揃えるのと同じ意図）。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from app.adapters.fx import FxAdapter
from app.batch.runner import JobResult
from app.config import settings
from app.db import repo
from app.db.engine import get_engine

logger = logging.getLogger(__name__)

_SOURCE = "fx:USDJPY"  # fetch_meta の source キー


def _start_date(*, full_backfill: bool, today: str) -> str:
    """取得開始日を決める（fetch_us_quotes._start_date 同型・単一ペアのカーソル）。

    ADR-057: FX 差分取得の再開点。
    full_backfill: today − backfill_years 年。
    差分: fetch_meta['fx:USDJPY'].last_fetched_date の翌日。
    fetch_meta 不在/NULL なら full 相当（today − backfill_years 年）。
    """
    last = date.fromisoformat(today)
    if full_backfill:
        return last.replace(year=last.year - settings.backfill_years).isoformat()

    with get_engine().connect() as conn:
        meta = repo.get_fetch_meta(conn, _SOURCE)
        last_fetched = meta.get("last_fetched_date") if meta else None

    if last_fetched is None:
        return last.replace(year=last.year - settings.backfill_years).isoformat()
    return (date.fromisoformat(last_fetched) + timedelta(days=1)).isoformat()


def run(*, full_backfill: bool = False, adapter: FxAdapter | None = None) -> JobResult:
    """USDJPY の日足 FX レートを取得し fx_rates / fetch_meta を前進させる（Phase 7(B-2)）。

    ADR-010/057: FxAdapter 経由で取得（直結ハードコードしない）。
    ADR-002: 書き込みは UPSERT で冪等（再取得で重複しない）。
    ADR-018: 例外はジョブ境界で握り JobResult(ok=False) に畳む。

    `adapter` 引数でテスト用 fake を注入できる（実 HTTP に出ない＝testing-strategy）。
    0 行でも ok=True（休場日は差分なしで正常）。取得失敗は ok=False。
    """
    today = date.today().isoformat()
    start = _start_date(full_backfill=full_backfill, today=today)

    if start > today:
        return JobResult(
            name="fetch_fx_rates",
            ok=True,
            rows=0,
            detail=f"取得不要（start={start} > {today}）",
        )

    adapter = adapter or FxAdapter()

    try:
        rows = adapter.fetch_rates(pair="USDJPY", from_=start, to=today)
    except Exception as exc:  # noqa: BLE001 — ジョブ境界で握り runner に返す
        logger.exception("fetch_fx_rates: FX レート取得に失敗（start=%s〜%s）", start, today)
        return JobResult(
            name="fetch_fx_rates",
            ok=False,
            rows=0,
            detail=f"FX レート取得失敗: {exc}",
        )

    # date が欠けた行は弾く（PK が NULL だと UPSERT が壊れる・fetch_us_quotes 同型）。
    rows = [r for r in rows if r.get("date")]

    if not rows:
        # 0 行は休場日等の正常ケース。カーソルを today まで前進させ再実行で空振りを繰り返さない。
        repo.upsert_fetch_meta(_SOURCE, today)
        logger.info(
            "fetch_fx_rates: 0 行（start=%s〜%s・休場等）。fetch_meta を前進。", start, today
        )
        return JobResult(
            name="fetch_fx_rates",
            ok=True,
            rows=0,
            detail=f"0 行（start={start}〜{today}）",
        )

    upserted = repo.upsert_fx_rates(rows)
    max_date = max(r["date"] for r in rows)
    repo.upsert_fetch_meta(_SOURCE, max_date)

    logger.info(
        "fetch_fx_rates: %s〜%s・%d 行 UPSERT（max_date=%s%s）",
        start,
        today,
        upserted,
        max_date,
        "・full_backfill" if full_backfill else "",
    )
    return JobResult(
        name="fetch_fx_rates",
        ok=True,
        rows=upserted,
        detail=(
            f"{upserted} 行 UPSERT（start={start}〜{today}"
            f"{'・full_backfill' if full_backfill else ''}）"
        ),
    )
