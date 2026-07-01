"""主要指数取得ジョブ — IndexAdapter で日次終値を取得し index_quotes に UPSERT する。

phase2-spec.md §3.1。`config.index_symbol_list` のシンボルごとに
`IndexAdapter.fetch_index_quotes` を呼び、差分は
`fetch_meta['index_quotes:<symbol>']` で管理する（既存 `fetch_meta` 流儀・ADR-018）。
例外はジョブ境界で握り `JobResult(ok=False)` で返す（fetch_quotes.py の構造を踏襲）。
書き込みは UPSERT で冪等（ADR-002）。
"""

from __future__ import annotations

import logging
from datetime import date

from app.adapters.index import US_SECTOR_ETFS, IndexAdapter, IndexAdapterError
from app.batch import state
from app.batch.jobs._cursor import resolve_differential_start
from app.batch.runner import JobResult
from app.config import settings
from app.db import repo
from app.db.engine import get_engine

logger = logging.getLogger(__name__)

_SOURCE_PREFIX = "index_quotes"  # fetch_meta の source キー接頭辞


def _target_symbols() -> list[str]:
    """取得対象シンボルを返す＝主要指数（config）＋米国業種 ETF 11 本（ADR-010・Phase 7）。

    config.index_symbol_list（^SPX/^NKX/^TPX 等）に US_SECTOR_ETFS（XLK 等）を足す。
    canonical=素ティッカー。重複は順序を保って排除する（config に ETF を二重指定しても安全）。
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for symbol in [*settings.index_symbol_list, *US_SECTOR_ETFS]:
        if symbol not in seen:
            seen.add(symbol)
            ordered.append(symbol)
    return ordered


def _source_key(symbol: str) -> str:
    """シンボルごとの fetch_meta source キーを返す（例: 'index_quotes:^SPX'）。"""
    return f"{_SOURCE_PREFIX}:{symbol}"


def _start_date_for_symbol(symbol: str, today: str) -> str:
    """シンボルの取得開始日を fetch_meta から決める（差分取得・ADR-018 部分失敗からの再開）。

    fetch_meta 未存在 → BACKFILL_YEARS 分を頭から。
    fetch_meta あり → last_fetched_date に重ねた地点から（鮮度プローブ）。
    重ね・冪等の意図と重ね日数は resolve_differential_start（_cursor.py）に集約（ADR-018/002）。
    """
    last = date.fromisoformat(today)
    backfill_start = last.replace(year=last.year - settings.backfill_years).isoformat()
    with get_engine().connect() as conn:
        meta = repo.get_fetch_meta(conn, _source_key(symbol))
        last_fetched = meta.get("last_fetched_date") if meta else None
    return resolve_differential_start(last_fetched, backfill_start=backfill_start)


def run() -> JobResult:
    """主要指数の日次終値を取得し index_quotes / fetch_meta を前進させる（spec §3.1）。

    取得対象は config.index_symbol_list（^SPX/^NKX/^TPX 等）＋米国業種 ETF 11 本
    （US_SECTOR_ETFS・Phase 7 リードラグ用）。各シンボルを差分取得して UPSERT する。
    シンボルごとに例外が発生しても後続シンボルを継続する。**全試行シンボルが失敗したとき
    （＝総崩れ）だけ ok=False** とし、1 本でも成功すれば ok=True（一部失敗は detail に内訳を残す）。
    取得手段の無いシンボル（例: Free プランで取れない指数）1 本のために毎晩バッチ失敗アラートを
    鳴らさないための割り切り。落ちたシンボルは notify_digest が fetch_meta 鮮度から拾い可視化する。
    例外はジョブ境界で握り、総崩れ時のみ runner が Discord 通知する（ADR-018）。
    """
    today = date.today().isoformat()
    symbols = _target_symbols()

    total_rows = 0
    attempted = 0  # 実際に取得を試みた数（最新でスキップした分は含めない）
    failed_symbols: list[str] = []

    adapter = IndexAdapter()

    # シンボル境界で should_stop を見て中断する（stop_aware・ADR-036 追補／停止フラグはファイル＝
    # ADR-070）。取れた分は fetch_meta 前進済み＝冪等再開できる（ADR-018）。
    for symbol in state.stop_aware(symbols):
        start = _start_date_for_symbol(symbol, today)
        if start > today:
            logger.info(
                "fetch_index: %s は最新（start=%s > today=%s）。スキップ。", symbol, start, today
            )
            continue

        attempted += 1
        try:
            rows = adapter.fetch_index_quotes(symbol, from_=start, to=today)
            # symbol/date が欠けた行は弾く（PK が NULL だと UPSERT が壊れる）
            rows = [r for r in rows if r.get("symbol") and r.get("date")]
            if rows:
                total_rows += repo.upsert_index_quotes(rows)
                # fetch_meta を最新取得日まで前進させる（ADR-018 部分失敗からの再開）
                max_date = max(r["date"] for r in rows)
                repo.upsert_fetch_meta(_source_key(symbol), max_date)
            else:
                # 空配列でも fetch_meta を today まで前進させる（再実行で空振りを繰り返さない）
                repo.upsert_fetch_meta(_source_key(symbol), today)
            logger.info("fetch_index: %s %s〜%s・%d 行 UPSERT", symbol, start, today, len(rows))
        except (IndexAdapterError, Exception) as exc:  # noqa: BLE001 — ジョブ境界で握る
            logger.exception("fetch_index: %s が失敗（start=%s）", symbol, start)
            # 直近試行の失敗を記録（last_fetched_date は据え置き）。
            # digest が「取得できなかった指数」を情報行に出す（ADR-018）。
            repo.mark_fetch_attempt_failed(_source_key(symbol))
            failed_symbols.append(f"{symbol}: {exc}")

    # 総崩れ（試行した全シンボルが失敗）のときだけ失敗扱い＝runner が Discord 通知（ADR-018）。
    if attempted > 0 and len(failed_symbols) == attempted:
        return JobResult(
            name="fetch_index",
            ok=False,
            rows=total_rows,
            detail=f"全 {attempted} シンボル取得失敗: {', '.join(failed_symbols)}",
        )

    # 一部失敗（1 本でも成功）or 全スキップは成功扱い。失敗があれば内訳を detail に残す。
    detail = f"シンボル {attempted}/{len(symbols)} 件試行・{total_rows} 行 UPSERT（〜{today}）"
    if failed_symbols:
        detail += f"・取得不可 {len(failed_symbols)} 件: {', '.join(failed_symbols)}"
    return JobResult(
        name="fetch_index",
        ok=True,
        rows=total_rows,
        detail=detail,
    )
