"""Phase 0 バックフィル（手動・数銘柄）＋ Phase 1 夜間バッチの手動起動。

    uv run python -m app.scripts.backfill            # 既定 3 銘柄（Phase 0 互換）
    uv run python -m app.scripts.backfill 7203 6758  # 任意の銘柄（Phase 0 互換）
    uv run python -m app.scripts.backfill --nightly  # 全銘柄バッチ（full_backfill・Phase 1）

J-Quants V2 → SQLite（stocks / daily_quotes）を UPSERT で冪等に投入する。
再実行しても重複行は増えない（ADR-002・Phase 0 完了条件）。`--nightly` は `run_nightly`
（全銘柄バッチ・signals 計算）を full_backfill で呼ぶ薄い分岐（spec §3.3）。flock で同居
スケジューラと相互排他される（spec §3.5）。
"""

from __future__ import annotations

import sys

from app.adapters.jquants import JQuantsError
from app.db.engine import init_db
from app.db.repo import upsert_daily_quotes, upsert_stocks
from app.services.jquants_config import build_jquants_adapter

# 既定の 3 銘柄（縦スライス検証用）。トヨタ / ソニーG / ソフトバンクG。
DEFAULT_CODES = ["7203", "6758", "9984"]


def backfill(codes: list[str]) -> int:
    init_db()
    adapter = build_jquants_adapter()

    print(f"▶ 銘柄マスタ取得: {codes}")
    stocks = adapter.fetch_master(codes)
    n_stocks = upsert_stocks(stocks)
    print(f"  stocks upsert: {n_stocks} 行")

    total_quotes = 0
    for code in codes:
        quotes = adapter.fetch_daily_quotes(code)
        # code が空の行（正規化漏れ）は弾く。PK が NULL だと UPSERT が壊れるため。
        quotes = [q for q in quotes if q.get("code") and q.get("date")]
        n = upsert_daily_quotes(quotes)
        total_quotes += n
        print(f"  {code}: daily_quotes upsert {n} 行")

    print(f"✔ 完了: stocks {n_stocks} 行 / daily_quotes 合計 {total_quotes} 行")
    return total_quotes


def run_nightly_cli() -> int:
    """`--nightly`: 全銘柄バッチを full_backfill で 1 回流す（spec §3.3）。

    既存の 3 銘柄バックフィルとは別経路。run_nightly 内で init_db・ロック取得・通知まで完結する。
    """
    # 重い依存（pandas/apscheduler 連鎖）を Phase 0 互換経路に引き込まないよう遅延 import する。
    from app.batch import run_nightly
    from app.batch.lock import BatchAlreadyRunning
    from app.db.engine import init_db

    init_db()
    try:
        results = run_nightly(full_backfill=True)
    except BatchAlreadyRunning as exc:
        print(f"✖ 既にバッチが実行中です: {exc}", file=sys.stderr)
        return 1

    for r in results:
        mark = "✔" if r.ok else "✖"
        print(f"  {mark} {r.name}: rows={r.rows} {r.detail}")
    return 0 if all(r.ok for r in results) else 1


def main() -> int:
    args = sys.argv[1:]
    if "--nightly" in args:
        return run_nightly_cli()

    codes = args or DEFAULT_CODES
    try:
        backfill(codes)
    except JQuantsError as exc:
        print(f"✖ J-Quants 取得に失敗: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
