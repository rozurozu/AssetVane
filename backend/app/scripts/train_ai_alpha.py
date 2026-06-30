"""AI Alpha Scorer の学習を回す CLI（別 PC＝開発機のコンテナ内で実行・ADR-006）。

設計の真実: docs/ml-training.md（再現手順）／docs/phase-specs/phase5-spec.md §3。

- **学習は別 PC**（ADR-006）。ラズパイ本番（推論のみ）では呼ばない。開発機の Docker で
  `make train-ai-alpha`（= `docker compose run --rm --no-deps backend uv run python -m
  app.scripts.train_ai_alpha`）から起動し、現用 DB（named volume `assetvane-db`）を**読み取り
  専用**で読む。バックアップ吸い出し不要（ADR-066）。
- **DB は読み取り専用接続（`?mode=ro`）で開く**。これで WAL 下でも dev backend の書き込みと
  ロック競合しない（ADR-002）。学習は SELECT のみで DB を一切書かない。
- 出力 `.pkl`＋メタは `settings.ml_model_dir`（`./models` → コンテナでは bind mount で
  ホストの `backend/models/` に出る）。次の夜間バッチで `score_ai_alpha` が `load_active`
  → 推論に使う（ml-training.md §4）。本番（ラズパイ）へは `.pkl` を rsync 配布。
- **数字を作らない**（ADR-014）: 特徴量・ラベルが組めない (code, 開示) は build_training_set が
  除外（skip）。学習はテスト済み純関数（quant/ml/*）に委譲し、本スクリプトは配管だけ持つ。

使い方:
    uv run python -m app.scripts.train_ai_alpha                 # 現用 DB・既定（horizon=60）
    uv run python -m app.scripts.train_ai_alpha --horizon 20    # ラベル窓を変える
    uv run python -m app.scripts.train_ai_alpha --db /path/to/assetvane.db  # 別 DB を指定
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import UTC, datetime

import pandas as pd

from app.config import settings
from app.quant.ml.train import build_training_set, save_model, train_model, walk_forward_cv


def _resolve_db_path(override: str | None) -> str:
    """読む DB のファイルパスを決める（override 優先・既定は settings.database_path）。

    コンテナ内では `./data/assetvane.db` が symlink（/app/data → /data）経由で named volume
    `assetvane-db` に解決される（engine.py のコメント・ADR-021/060）。ここは engine と同じ
    database_path を使い、読み取り専用接続で開く（書きは一切しない・ADR-002）。
    """
    return override if override else settings.database_path


def _load_inputs(db_path: str, bench_symbol: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    """現用 DB を読み取り専用で開き、学習に要る 3 系統（財務・日足・ベンチ）を読む（§1）。

    bench_symbol: 対ベンチ超過リターンの基準。既定 `^TPX`（TOPIX 指数）だが、Free プランで指数が
    取れない場合は TOPIX 連動 ETF（例 `1306.T`・backfill_topix_benchmark で投入）を指定する。
    """
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        fin = pd.read_sql(
            "SELECT code,disclosed_date,fiscal_period,net_sales,operating_profit,profit,eps,bps "
            "FROM financials ORDER BY code,disclosed_date",
            con,
        )
        px = pd.read_sql("SELECT code,date,adj_close FROM daily_quotes ORDER BY code,date", con)
        bench = pd.read_sql(
            "SELECT date,close FROM index_quotes WHERE symbol=? ORDER BY date",
            con,
            params=(bench_symbol,),
        ).set_index("date")["close"]
    finally:
        con.close()
    return fin, px, bench


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AI Alpha Scorer 学習（別 PC・ADR-006）")
    parser.add_argument("--db", default=None, help="読む DB パス（既定=settings.database_path）")
    parser.add_argument(
        "--bench-symbol",
        default="^TPX",
        help="超過リターンのベンチ symbol（既定 ^TPX・Free は 1306.T 等の TOPIX ETF）",
    )
    parser.add_argument("--horizon", type=int, default=60, help="ラベルの保有営業日数（既定 60）")
    parser.add_argument("--out", default=None, help="出力先（既定=settings.ml_model_dir）")
    parser.add_argument(
        "--splits", type=int, default=5, help="walk-forward CV の fold 数（既定 5）"
    )
    parser.add_argument(
        "--trained-at", default=None, help="モデル日付（既定=今日 UTC・ファイル名 stem に使う）"
    )
    parser.add_argument("--notes", default="", help="メタに残す自由記述")
    args = parser.parse_args(argv)

    import lightgbm  # 学習時のみ要る（メタの lib_version に記録）。

    db_path = _resolve_db_path(args.db)
    out_dir = args.out if args.out is not None else settings.ml_model_dir
    trained_at = args.trained_at or datetime.now(UTC).strftime("%Y-%m-%d")

    print(
        f"▶ DB(読み取り専用)={db_path}  horizon={args.horizon}  "
        f"bench={args.bench_symbol}  out={out_dir}"
    )
    fin, px, bench = _load_inputs(db_path, args.bench_symbol)
    print(
        f"  入力: financials={len(fin)} daily_quotes={len(px)} "
        f"bench[{args.bench_symbol}]={len(bench)}"
    )
    if bench.empty:
        raise SystemExit(
            f"ベンチ({args.bench_symbol})が空。index_quotes に無いと超過リターンを組めない。"
            " Free なら `make backfill-topix` で TOPIX ETF を入れてから --bench-symbol 1306.T。"
        )

    # point-in-time でサンプル化（with_dates で CV 用の開示日も受け取る）。
    x, y, names, dates = build_training_set(
        fin, px, bench, label_horizon_days=args.horizon, with_dates=True
    )
    print(f"  学習サンプル数={len(x)}")
    if len(x) < 50:
        raise SystemExit(f"サンプルが少なすぎる（{len(x)}）。^TPX の期間や財務の充足を確認。")

    # 1) walk-forward CV（汎化性能の目安・リーク無し）。
    cv = walk_forward_cv(x, y, dates, names, n_splits=args.splits)
    rmse_m, rmse_s = cv.get("cv_rmse_mean"), cv.get("cv_rmse_std")
    ic_m, ic_s = cv.get("cv_ic_mean"), cv.get("cv_ic_std")
    print(
        f"  [CV] folds={cv['n_folds']:.0f} RMSE={rmse_m:.4f}±{rmse_s:.4f} IC={ic_m:.4f}±{ic_s:.4f}"
    )

    # 2) 全データで本学習（in-sample metrics は smoke）。
    model, metrics = train_model(x, y, names)
    print(
        f"  [fit] in-sample RMSE={metrics['rmse']:.4f} "
        f"IC={metrics['ic']:.4f} n={metrics['n_samples']:.0f}"
    )

    # 3) 保存（.pkl＋メタ＋latest）。
    pkl_path, json_path = save_model(
        model,
        names,
        out_dir=out_dir,
        trained_at=trained_at,
        target=f"excess_return_{args.horizon}d",
        lib_version=lightgbm.__version__,
        notes=args.notes
        or f"horizon={args.horizon} samples={len(x)} cv_ic={cv.get('cv_ic_mean'):.4f}",
    )
    print(f"✅ 保存: {pkl_path}\n          {json_path}")
    print("   次の夜間バッチで score_ai_alpha が load_active して推論に使う（ml-training.md §4）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
