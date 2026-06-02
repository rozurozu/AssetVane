"""Phase 0 バックフィル（手動・数銘柄）。

    uv run python -m app.scripts.backfill            # 既定 3 銘柄
    uv run python -m app.scripts.backfill 7203 6758  # 任意の銘柄

J-Quants V2 → SQLite（stocks / daily_quotes）を UPSERT で冪等に投入する。
再実行しても重複行は増えない（ADR-002・Phase 0 完了条件）。全銘柄・差分・cron は Phase 1。
"""

from __future__ import annotations

import sys

from app.adapters.jquants import JQuantsAdapter, JQuantsError
from app.db.engine import init_db
from app.db.repo import upsert_daily_quotes, upsert_stocks

# 既定の 3 銘柄（縦スライス検証用）。トヨタ / ソニーG / ソフトバンクG。
DEFAULT_CODES = ["7203", "6758", "9984"]


def backfill(codes: list[str]) -> int:
    init_db()
    adapter = JQuantsAdapter()

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


def main() -> int:
    codes = sys.argv[1:] or DEFAULT_CODES
    try:
        backfill(codes)
    except JQuantsError as exc:
        print(f"✖ J-Quants 取得に失敗: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
