"""TOPIX ベンチ（^TPX 代替）の履歴バックフィル — Free プランで TOPIX 指数が取れない穴を埋める。

設計の真実: docs/ml-training.md §1（ベンチ）／ADR-039/040（IndexAdapter・指数取得）。

- Phase 5 の学習ラベルは「対 TOPIX 60 営業日超過リターン」。だが TOPIX 指数（`^TPX`）は J-Quants
  Light 以上でしか取れず（Free=403）、Yahoo/Stooq にも有効な TOPIX 指数シンボルが無い
  （`adapters/index.py` の注記）。そこで **TOPIX 連動 ETF（既定 `1306.T`＝NEXT FUNDS TOPIX ETF）**
  の配当調整後 close を IndexAdapter（YahooIndexSource・ETF は恒等取得）で取り、`index_quotes` に
  UPSERT して学習ベンチに使う（ADR-010・米国業種 ETF と同じ恒等取得経路）。
- これは「指数の代替プロキシ」。総リターン連動なので相対超過リターンのベンチとして妥当。Light 以上に
  したら本物の `^TPX` を別途入れて学習側 `--bench-symbol` を戻せばよい（データは別 symbol で共存）。

使い方（コンテナ内・現用 volume DB へ書き込む。`make backfill-topix` 推奨）:
    uv run python -m app.scripts.backfill_topix_benchmark
    uv run python -m app.scripts.backfill_topix_benchmark --symbol 1306.T --from 2020-01-01
"""

from __future__ import annotations

import argparse

from app.adapters.index import IndexAdapter, IndexAdapterError
from app.db.engine import init_db
from app.db.repo import upsert_index_quotes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TOPIX ベンチ（ETF プロキシ）の履歴バックフィル")
    parser.add_argument(
        "--symbol", default="1306.T", help="TOPIX 連動 ETF の Yahoo シンボル（既定 1306.T）"
    )
    parser.add_argument(
        "--from",
        dest="from_",
        default="2022-01-01",
        help="取得開始日 YYYY-MM-DD（既定 2022-01-01）",
    )
    parser.add_argument("--to", default=None, help="取得終了日 YYYY-MM-DD（既定=最新）")
    args = parser.parse_args(argv)

    init_db()
    adapter = IndexAdapter()
    print(f"▶ TOPIX ベンチ取得: symbol={args.symbol} from={args.from_} to={args.to or '最新'}")
    try:
        rows = adapter.fetch_index_quotes(args.symbol, args.from_, args.to)
    except IndexAdapterError as exc:
        raise SystemExit(f"取得失敗（IndexAdapter）: {exc}") from exc
    if not rows:
        raise SystemExit("0 行。symbol/期間を確認するのだ。")

    n = upsert_index_quotes(rows)
    dates = sorted(r["date"] for r in rows)
    print(f"✅ index_quotes に {n} 行 UPSERT（{dates[0]}..{dates[-1]}・symbol={args.symbol}）")
    print(f'   学習: make train-ai-alpha ARGS="--bench-symbol {args.symbol}"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
