"""夜間バッチのジョブ群（spec §3.3）。

`NIGHTLY_JOBS` が**実行順の単一の真実**。後続 Phase はここに append する。
順序の意図:
  マスタ → 日足取得 → 指数取得 → 財務取得 → シグナル計算（当日の事実が揃ってから算出）
  → 資産スナップショット（今日の株価が確定してから評価額を焼く）。
Phase 2 で fetch_index / fetch_financials を calc_signals の前に挿入（phase2-spec.md §3）。
Phase 2 で snapshot_assets を末尾に追加（phase2-spec.md §3.3・app レーン担当）。
"""

from __future__ import annotations

from app.batch.jobs import (
    calc_signals,
    fetch_financials,
    fetch_index,
    fetch_quotes,
    snapshot_assets,
    sync_master,
)

NIGHTLY_JOBS = [
    sync_master.run,
    fetch_quotes.run,
    fetch_index.run,
    fetch_financials.run,
    calc_signals.run,
    snapshot_assets.run,  # Phase 2: 今日の株価確定後に評価額を焼く（phase2-spec.md §3.3）
]
