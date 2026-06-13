"""米株 OHLCV 取得ジョブ — UsEquityAdapter で日足を取り us_daily_quotes に焼く（Phase 7(B-1)）。

ADR-031/039（米市場分離・UsEquityAdapter）・ADR-002（UPSERT 冪等）・ADR-018（部分失敗の握り）。
日本株 fetch_quotes.py をミラーした米株版（既存無改変）。差分は **全銘柄共通カーソル**
`fetch_meta['us_daily_quotes']`（last_fetched_date）で管理する（fetch_quotes.py 同型・日付一括の
流儀に倣い、全銘柄まとめて 1 つの再開境界を持つ）。

取得アーキ（grill 確定・ADR-039(B)）:
  - 初回（full_backfill or カーソル無し）= today - BACKFILL_YEARS 年から全銘柄を取り直す。
  - 差分 = カーソルから _REFETCH_OVERLAP_DAYS 日重ねた地点から today まで（鮮度プローブ・
    fetch_index.py 同型）。アダプタは 0 行で raise する契約（ADR-018: 黙って 0 行にしない）の
    ため、週末でも直近営業日が窓に必ず入るよう重ねて取り直す（tasks/review-2026-06-12.md C-1）。
  - シンボルを settings.us_quotes_batch_size ごとに分割し、1 バッチ分の OHLCV を 1 トランザクション
    （`with get_engine().begin()`）で UPSERT する（巨大トランザクション/長時間メモリ滞留を避ける）。
    adapter.fetch_quotes は 1 シンボル単位なのでバッチ内はシンボルループだが、UPSERT をバッチ境界で
    区切ることで「yf.download 一括 → まとめ書き」の利点（書き込み回数の削減）を取る。

部分失敗の握り（ADR-018・batch-pattern）: 1 シンボルが例外でも他を止めない。**全試行シンボルが
失敗（総崩れ）のときだけ ok=False**、1 本でも成功すれば ok=True（fetch_index.py 同型の割り切り）。
カーソルは全シンボル処理後に「今回取得できた最大 date」へ前進させる（取れた範囲で境界を進める）。
"""

from __future__ import annotations

import logging
from datetime import date

from app.adapters.us_equity import UsEquityAdapter
from app.batch.jobs._cursor import resolve_differential_start
from app.batch.runner import JobResult
from app.config import settings
from app.db import repo
from app.db.engine import get_engine

logger = logging.getLogger(__name__)

_SOURCE = "us_daily_quotes"  # fetch_meta の source キー（全銘柄共通カーソル）


def _start_date(*, full_backfill: bool, today: str) -> str:
    """取得開始日を決める（全銘柄共通カーソル・full_backfill で頭から取り直す）。

    full_backfill: today − BACKFILL_YEARS 年（カーソルを読まず頭から）。
    差分: fetch_meta['us_daily_quotes'].last_fetched_date に重ねた地点（鮮度プローブ）。
    重ね・冪等・C-1/C-2 の意図と重ね日数は resolve_differential_start（_cursor.py）に集約
    （ADR-018/002）。アダプタの「0 行＝raise」が偽失敗にならないよう重ねて取り直す。
    """
    last = date.fromisoformat(today)
    backfill_start = last.replace(year=last.year - settings.backfill_years).isoformat()
    if full_backfill:
        return backfill_start
    with get_engine().connect() as conn:
        meta = repo.get_fetch_meta(conn, _SOURCE)
        last_fetched = meta.get("last_fetched_date") if meta else None
    return resolve_differential_start(last_fetched, backfill_start=backfill_start)


def _batches(symbols: list[str], size: int) -> list[list[str]]:
    """symbols を size ごとに分割する（バッチ境界で UPSERT を区切るため）。"""
    size = max(1, size)
    return [symbols[i : i + size] for i in range(0, len(symbols), size)]


def run(*, full_backfill: bool = False, adapter: UsEquityAdapter | None = None) -> JobResult:
    """全 us_stocks の日足を取得し us_daily_quotes / fetch_meta を前進させる（Phase 7(B-1)）。

    `adapter` 引数でテスト用 fake を注入できる（実 HTTP に出さない＝testing-strategy）。
    例外は個別シンボル境界で握り後続を止めない（ADR-018）。総崩れのみ ok=False。
    """
    today = date.today().isoformat()
    start = _start_date(full_backfill=full_backfill, today=today)
    if start > today:
        return JobResult(
            name="fetch_us_quotes", ok=True, rows=0, detail=f"取得不要（start={start} > {today}）"
        )

    try:
        with get_engine().connect() as conn:
            symbols = [r["symbol"] for r in repo.list_us_stocks(conn)]
    except Exception as exc:  # noqa: BLE001 — ジョブ境界で握り runner に返す
        logger.exception("fetch_us_quotes: ユニバース取得に失敗")
        return JobResult(
            name="fetch_us_quotes", ok=False, rows=0, detail=f"ユニバース取得失敗: {exc}"
        )

    if not symbols:
        return JobResult(
            name="fetch_us_quotes", ok=True, rows=0, detail="us_stocks が空（ユニバース未同期）"
        )

    adapter = adapter or UsEquityAdapter()
    total_rows = 0
    attempted = 0
    failed: list[str] = []
    max_date: str | None = None

    for batch in _batches(symbols, settings.us_quotes_batch_size):
        batch_rows: list[dict] = []
        for symbol in batch:
            attempted += 1
            try:
                rows = adapter.fetch_quotes(symbol, from_=start, to=today)
            except Exception as exc:  # noqa: BLE001 — シンボル境界で握り後続を止めない（ADR-018）
                logger.info("fetch_us_quotes: %s 取得失敗→スキップ: %s", symbol, exc)
                failed.append(f"{symbol}: {exc}")
                continue
            # symbol/date が欠けた行は弾く（PK が NULL だと UPSERT が壊れる・fetch_quotes 同型）。
            rows = [r for r in rows if r.get("symbol") and r.get("date")]
            if rows:
                batch_rows.extend(rows)
                batch_max = max(r["date"] for r in rows)
                if max_date is None or batch_max > max_date:
                    max_date = batch_max

        if batch_rows:
            # 1 バッチ分を 1 トランザクションで UPSERT（W2＝呼び出し側が begin を所有）。
            try:
                with get_engine().begin() as conn:
                    total_rows += repo.upsert_us_daily_quotes(conn, batch_rows)
            except Exception as exc:  # noqa: BLE001 — バッチ書き込み境界で握り後続バッチを止めない
                logger.exception("fetch_us_quotes: バッチ UPSERT に失敗（%d 行）", len(batch_rows))
                failed.append(f"batch({len(batch_rows)}行): {exc}")

    # 取れた最大 date までカーソルを前進させる（取れた範囲で再開境界を進める・ADR-018）。
    if max_date is not None:
        repo.upsert_fetch_meta(_SOURCE, max_date)

    # 総崩れ（試行した全シンボルが失敗）のときだけ失敗扱い（fetch_index.py 同型の割り切り）。
    if attempted > 0 and len(failed) >= attempted:
        return JobResult(
            name="fetch_us_quotes",
            ok=False,
            rows=total_rows,
            detail=f"全 {attempted} シンボル取得失敗: {'; '.join(failed[:10])}",
        )

    detail = (
        f"シンボル {attempted} 件試行・{total_rows} 行 UPSERT（start={start}〜{today}"
        f"{'・full_backfill' if full_backfill else ''}）"
    )
    if failed:
        detail += f"・失敗 {len(failed)} 件: {'; '.join(failed[:5])}"
    return JobResult(name="fetch_us_quotes", ok=True, rows=total_rows, detail=detail)
