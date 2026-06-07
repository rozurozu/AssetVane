"""夜間バッチのジョブ群（spec §3.3）。

`NIGHTLY_JOBS` が**実行順の単一の真実**。後続 Phase はここに append する。
順序の意図:
  マスタ → 日足取得 → 指数取得 → 財務取得 → バリュエーション計算 → シグナル計算
  （当日の事実が揃ってから算出）→ 資産スナップショット（今日の株価が確定してから評価額を焼く）。
Phase 2 で fetch_index / fetch_financials を calc_signals の前に挿入（phase2-spec.md §3）。
Phase 2 で snapshot_assets を末尾に追加（phase2-spec.md §3.3・app レーン担当）。
ADR-031 で calc_valuation を fetch_financials の後・calc_signals の前に挿入（screen の土台）。
"""

from __future__ import annotations

from app.batch.jobs import (
    calc_lead_lag,
    calc_signals,
    calc_valuation,
    fetch_financials,
    fetch_general_news,
    fetch_index,
    fetch_quotes,
    investigate_dossier,
    notify_digest,
    run_advisor,
    score_ai_alpha,
    snapshot_assets,
    sync_master,
)

NIGHTLY_JOBS = [
    sync_master.run,
    fetch_quotes.run,
    fetch_index.run,
    fetch_financials.run,
    calc_valuation.run,  # ADR-031: 財務取得後に全銘柄のバリュエーションを焼く（screen の土台）
    calc_signals.run,
    # Phase 7: 日米業種リードラグを焼く。calc_signals の後・run_advisor の前に置き、夜の分析AI が
    # 当日の lead_lag を読めるようにする（SIG-FIN-036-13・US/JP 業種 ETF が揃ってから算出）。
    calc_lead_lag.run,
    # Phase 5: 学習済みモデルで ai_alpha を焼く。calc_signals の後・run_advisor の前に置き、
    # 夜の分析AI が当日の ai_alpha を読めるようにする（phase5-spec.md §4.4）。
    score_ai_alpha.run,
    snapshot_assets.run,  # Phase 2: 今日の株価確定後に評価額を焼く（phase2-spec.md §3.3）
    # ADR-034: 夜の分析AI の市況文脈材料として run_advisor の直前で一般ニュースを取得・保存。
    fetch_general_news.run,
    run_advisor.run,  # Phase 3: 事実が揃ってから夜の分析AI を回す（phase3-spec.md §5）
    investigate_dossier.run,  # Phase 4: watchlist を古い順に巡回しドシエ調査（phase4-spec.md §6）
    notify_digest.run,  # Phase 6: ⑦⑧＋夜AI 提案を 1 通の Discord digest に束ねる（phase6-spec §3）
]
