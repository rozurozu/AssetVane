"""テーブル定義（SQLAlchemy Core）。

設計は docs/data-model.md。**Phase 0 は `stocks` と `daily_quotes` のみ**。
`financials` / `signals` / 自分データ / AI 系・`fetch_meta`（差分取得管理）は、
それを使う Phase で同じ `metadata` に足していく。

列名は安定した内部名（snake_case）に固定する。J-Quants V2 は略記キー（O/H/L/C/Vo/AdjC…）
を返すため、外部キー名 → 内部列名の対応は adapters 側の正規化に閉じ込める（docs/jquants.md）。
single user 前提で `user_id` は持たない（ADR-001）。
"""

from __future__ import annotations

from sqlalchemy import (
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    UniqueConstraint,
)

metadata = MetaData()

# 上場銘柄マスタ。J-Quants V2 /v2/equities/master 由来（data-model.md §2）。
stocks = Table(
    "stocks",
    metadata,
    Column("code", String, primary_key=True),  # J-Quants の 5 桁コード（例 72030）
    Column("company_name", String),
    Column("sector33_code", String),
    Column("sector17_code", String),
    Column("market_code", String),
    Column("is_etf", Integer),  # ETF/REIT 判別フラグ（0/1）
    Column("updated_at", String),  # 取得日時（ISO8601 文字列）
)

# 日足四本値。最大行数になるテーブル。ETF も同居（data-model.md §2）。
# 再取得で重複しないよう (code, date) を主キーにし、UPSERT で冪等にする（ADR-002）。
daily_quotes = Table(
    "daily_quotes",
    metadata,
    Column("code", String, nullable=False),
    Column("date", String, nullable=False),  # 営業日 'YYYY-MM-DD'
    Column("open", Float),
    Column("high", Float),
    Column("low", Float),
    Column("close", Float),
    Column("volume", Float),
    Column("adj_close", Float),  # 調整後終値（分割・併合調整）
    PrimaryKeyConstraint("code", "date", name="pk_daily_quotes"),
    Index("ix_daily_quotes_code", "code"),
    Index("ix_daily_quotes_date", "date"),
)

# 差分取得の進捗管理（Phase 1・data-model.md §6・ADR-018 の部分失敗からの再開）。
# `source` ごとに「取得済みの最終営業日」を持ち、夜間バッチが途中で落ちても翌回は続きから回せる。
# `updated_at` は「いつ最後にバッチが回ったか」を運用で見るための列（spec §2.1）。
fetch_meta = Table(
    "fetch_meta",
    metadata,
    Column(
        "source", String, primary_key=True
    ),  # 'daily_quotes' / 'stocks' / 将来 'index_quotes' 等
    Column("last_fetched_date", String),  # 'YYYY-MM-DD'（未取得なら NULL）
    Column("updated_at", String),  # ISO8601 UTC（この行の更新時刻）
)

# シグナル事前計算（Phase 1・data-model.md §4・ADR-002・ADR-026）。
# 夜間バッチが Python で計算した「事実」を焼き、AI Advisor / 一覧 UI はこれを読むだけ。
# `(date, code, signal_type)` に UNIQUE を張り、同じ夜の再実行でも冪等 UPSERT できる（spec §2.2）。
# `payload` は JSON 文字列（SQLite に JSON 型なし・既存方針通り json.dumps/json.loads）。
# `code` への FK は張らない（lead_lag では業種コードが入りうる・生データ流儀＝spec §2.2）。
signals = Table(
    "signals",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("date", String, nullable=False),  # 算出日 'YYYY-MM-DD'
    Column("code", String, nullable=False),  # 銘柄/業種コード（5桁）
    Column(
        "signal_type", String, nullable=False
    ),  # 'momentum'|'volume_spike'|'ai_alpha'(P5)|'lead_lag'(P7)
    Column("score", Float, nullable=False),  # 0.0〜1.0 の連続スコア・強度（ADR-026）
    Column("payload", String),  # JSON 文字列（指標値・根拠）
    UniqueConstraint("date", "code", "signal_type", name="uq_signals_date_code_type"),
    Index("ix_signals_date_type", "date", "signal_type"),  # 一覧・通知の主クエリ
    Index("ix_signals_code", "code"),  # 銘柄詳細横断
)

# ===== Phase 2: Portfolio Optimizer（phase2-spec.md §2.1・ADR-001/002/019） =====

# ポートフォリオ器（ADR-001: 単一ユーザー。当面 portfolio_id=1 の Default 1 つのみ）。
# seed 行は 0004_portfolio_and_assets マイグレーション内で挿入する（spec §2 注記）。
portfolios = Table(
    "portfolios",
    metadata,
    Column("portfolio_id", Integer, primary_key=True, autoincrement=True),
    Column("name", String, nullable=False),
    Column("created_at", String),  # ISO8601
)

# 取引記録（ADR-019: 一次データ。holdings はここから導出）。
# 自分データ（手入力）なので FK を張る（裁定 L-7：誤入力防止）。
transactions = Table(
    "transactions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("portfolio_id", Integer, ForeignKey("portfolios.portfolio_id"), nullable=False),
    Column("code", String, ForeignKey("stocks.code"), nullable=False),
    Column("side", String, nullable=False),  # 'buy' / 'sell'
    Column("shares", Float, nullable=False),
    Column("price", Float, nullable=False),  # 約定単価
    Column("fee", Float),  # 手数料（任意）
    Column("traded_at", String, nullable=False),  # 約定日 'YYYY-MM-DD'
    Index("ix_transactions_portfolio", "portfolio_id"),
    Index("ix_transactions_code", "code"),
)

# 保有銘柄（ADR-019: transactions からの導出値。直接編集しない）。
# (portfolio_id, code) に UNIQUE を張り、UPSERT キーとする。
holdings = Table(
    "holdings",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("portfolio_id", Integer, ForeignKey("portfolios.portfolio_id"), nullable=False),
    Column("code", String, ForeignKey("stocks.code"), nullable=False),
    Column("shares", Float, nullable=False),  # 導出: Σbuy.shares − Σsell.shares
    Column("avg_cost", Float),  # 導出: 移動平均取得単価
    UniqueConstraint("portfolio_id", "code", name="uq_holdings_portfolio_code"),
)

# 投資用待機現金（JPY・通貨列は Phase 7 まで持たない）。
cash = Table(
    "cash",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("balance", Float, nullable=False),
    Column("updated_at", String),
)

# 外部資産（投信・コモディティ等の手入力・proxy 指数付き）。
external_assets = Table(
    "external_assets",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String, nullable=False),  # 「オルカン」等
    Column("category", String),  # 投信/コモディティ等
    Column("value", Float),  # 評価額（手入力）
    Column("proxy_symbol", String),  # 概算 proxy（指数等）
    Column("monthly_contribution", Float),  # 毎月積立（任意）
    Column("as_of", String),  # 基準日
)

# 主要指数の水準（daily_quotes とは別粒度・別出所・IndexAdapter 供給）。
# (symbol, date) を複合主キーにし UPSERT で冪等（ADR-002）。
# `code` への FK は張らない（指数シンボルは stocks に存在しない・生データ流儀）。
index_quotes = Table(
    "index_quotes",
    metadata,
    Column("symbol", String, nullable=False),  # 'TOPIX' / '^GSPC' 等
    Column("date", String, nullable=False),  # 'YYYY-MM-DD'
    Column("close", Float),  # 終値（水準）
    PrimaryKeyConstraint("symbol", "date", name="pk_index_quotes"),
    Index("ix_index_quotes_symbol", "symbol"),
)

# 日次総資産（夜間バッチが焼く・1 日 1 行）。
# 保有評価額（遅延株価）＋ 現金 ＋ 外部資産の合計（spec §3.3）。
asset_snapshots = Table(
    "asset_snapshots",
    metadata,
    Column("date", String, primary_key=True),  # 'YYYY-MM-DD'
    Column("total_value", Float),
    Column("stock_value", Float),
    Column("cash_value", Float),
    Column("external_value", Float),
    Column("pnl", Float),
)

# 財務・決算（0005_financials・data-model.md §2・spec §2.1）。
# 自分データ（保有銘柄）なので code → stocks.code に FK を張る（裁定 L-7）。
# 実フィールド名は V2 財務エンドポイント実 API 確認待ち（jquants.md §6 要再確認）。
financials = Table(
    "financials",
    metadata,
    Column("code", String, ForeignKey("stocks.code"), nullable=False),
    Column("disclosed_date", String, nullable=False),  # 開示日 'YYYY-MM-DD'
    Column("fiscal_period", String, nullable=False),  # 会計期間（例 '2025Q1' / 'FY2024'）
    Column("net_sales", Float),  # 売上高（J-Quants summary: Sales）
    Column("operating_profit", Float),  # 営業利益（OP）
    Column("profit", Float),  # 純利益（NP）
    Column("eps", Float),  # EPS（PER = close / eps の素）
    Column("bps", Float),  # BPS（PBR = close / bps の素）
    # スクリーニング（バリュエーション導出）用に 0007_screening で追加。実機確認済みフィールド:
    #   dividend_per_share = 年間配当（予想 FDivAnn 優先・実績 DivAnn 代替）。利回り = dps / close
    #   shares_outstanding = 期末発行済株式数 ShOutFY、treasury_shares = 自己株式 TrShFY
    #   → 時価総額 = close * (shares_outstanding - treasury_shares)
    Column("dividend_per_share", Float),  # 年間配当（予想優先）
    Column("shares_outstanding", Float),  # 期末発行済株式数（ShOutFY）
    Column("treasury_shares", Float),  # 期末自己株式数（TrShFY）
    PrimaryKeyConstraint("code", "disclosed_date", "fiscal_period", name="pk_financials"),
    Index("ix_financials_code", "code"),
)

# ===== Phase 3: AI Advisor（phase3-spec.md §2.1・ADR-011〜016/028/029） =====

# 投資方針（ADR-013: 単一行を育てる。版管理機構なし）。id 固定の 1 行運用。
# 比率系（target_cash_ratio / max_position_weight / sector_caps）はすべて 0..1（決定2）。
# UI のみ ×100 して % 表示。最適化制約（optimize_portfolio）と同じ policy 行から作る。
# sector_caps / exclusions は JSON 文字列（SQLite に JSON 型なし・既存方針）。
policy = Table(
    "policy",
    metadata,
    Column("id", Integer, primary_key=True),  # 1 行運用（id 固定・autoincrement しない）
    Column("risk_tolerance", String),  # "低"/"中"/"高"
    Column("time_horizon", String),  # "短"/"中"/"長"
    Column("target_cash_ratio", Float),  # 0..1
    Column("max_position_weight", Float),  # 0..1
    Column("sector_caps", String),  # JSON {sector33_code: 0..1}
    Column("target_return", Float),  # 0..1（任意）
    Column("no_leverage", Integer),  # 0/1（bool）
    Column("exclusions", String),  # JSON ["7203", ...]
    Column("rationale", String),  # 自由文の理念（最適化に効かない・チャット即時更新可＝U-7）
    Column("updated_at", String),  # ISO8601
)

# 投資日記（ADR-011/029: 夜=1件/日 自動・チャットの会話要約昇格も当日 journal に書く）。
# source で 'nightly'（夜の分析AI）/ 'chat'（昼チャットの要約昇格）を区別（ADR-029）。
# situation_briefing / proposed_policy_change / policy_snapshot は JSON 文字列。
advisor_journal = Table(
    "advisor_journal",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("date", String, nullable=False),  # 'YYYY-MM-DD'
    Column("source", String, nullable=False, server_default="nightly"),  # 'nightly'/'chat'
    Column("situation_briefing", String),  # JSON（その日の事実・監査用・chat 昇格では null 可）
    Column("observations", String),  # AI 所見（自由文）
    Column("proposal", String),  # 当日の提案（自由文 or 参照）
    Column("proposed_policy_change", String),  # JSON 単一 {field,to}（任意 from/reason・ADR-030）
    Column("policy_snapshot", String),  # JSON（その時点の policy まるごと・履歴）
    Column("llm_model", String),  # 監査用（settings.llm_model）
    Column("created_at", String),  # ISO8601
    Index("ix_advisor_journal_date", "date"),
)

# 提案（ADR-001/019: 承認状態のみ。約定はしない）。
# depends_on で承認順制御（policy_change → buy。決定4/B-8）。
# journal_id は生成元 journal（夜）。チャット起票は null 可。
proposals = Table(
    "proposals",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("created_date", String, nullable=False),  # 'YYYY-MM-DD'
    Column("kind", String, nullable=False),  # "policy_change"/"buy"/"sell"/"rebalance"
    Column("body", String),  # JSON（kind 依存）
    Column("rationale", String),  # 根拠（AI の説明）
    Column("status", String, nullable=False, server_default="pending"),  # pending/approved/rejected
    Column("outcome", String),
    Column("resolved_at", String),
    Column("journal_id", Integer, ForeignKey("advisor_journal.id")),  # nullable
    Column("depends_on", Integer, ForeignKey("proposals.id")),  # nullable（承認順制御）
    Index("ix_proposals_status", "status"),
)

# LLM コストガードレール台帳（ADR-028・spec §7.1）。OpenRouter 実コスト（usage.cost）を積む。
# Ollama は cost 無し → $0 計上。単価表は自前で持たない。当月累計で warn/block を判定。
llm_usage = Table(
    "llm_usage",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("created_at", String, nullable=False),  # ISO8601（当月集計の起点）
    Column("source", String, nullable=False),  # "nightly"/"chat"/"dossier" 等
    Column("model", String),
    Column("tokens_in", Integer),
    Column("tokens_out", Integer),
    Column("cost_usd", Float, nullable=False, server_default="0"),  # OpenRouter usage.cost
    Index("ix_llm_usage_created_at", "created_at"),
)

# ===== スクリーニング（/stocks スクリーナー・ADR-031・0007_screening） =====
# 設計: 重い結合（daily_quotes × financials）を夜間ジョブ calc_valuation が
# 「1 銘柄 1 行」に畳んで valuation_snapshots に焼く。/stocks/screen は読み取り時に
# これを絞り込み、業種内/全体ランクは ~4000 行への window 関数で都度算出する（ADR-026）。
# AI には数値を計算させず Python が事実を計算する（ADR-014/016）。
# 値の鮮度は夜間バッチ更新と同じ（daily_quotes も夜間更新のため）。

# バリュエーション・スナップショット（1 銘柄最新 1 行・code を PK にして最新のみ保持）。
# 派生比率（per/pbr/market_cap/dividend_yield）＋根拠の素（close/eps/bps/配当/株数）を持つ。
# 業種/市場/名称は焼かず、読み取り時に stocks へ JOIN して補う（repo 規約）。
valuation_snapshots = Table(
    "valuation_snapshots",
    metadata,
    Column("code", String, ForeignKey("stocks.code"), primary_key=True),
    Column("as_of_date", String, nullable=False),  # 採用した株価の営業日 'YYYY-MM-DD'
    Column("close", Float),  # 採用終値（adj 前の素の終値）
    Column("eps", Float),  # 採用財務の EPS
    Column("bps", Float),  # 採用財務の BPS
    Column("dividend_per_share", Float),  # 採用財務の年間配当（予想優先）
    Column("shares_net", Float),  # 発行済 - 自己株（時価総額の素）
    Column("per", Float),  # close / eps
    Column("pbr", Float),  # close / bps
    Column("market_cap", Float),  # close * shares_net
    Column("dividend_yield", Float),  # dividend_per_share / close（0..1）
    Column("fin_disclosed_date", String),  # 採用した財務の開示日（監査・どの決算を使ったか）
    Column("updated_at", String),  # ISO8601（この行を焼いた時刻）
)

# 保存スクリーニング条件（ADR-001: 単一ユーザーなので user_id を持たない・複数行は可）。
# criteria_json は UI のフィルタ条件まるごと（範囲・ランク・業種・市場・sort）。
# policy/watchlist と同じく JSON(TEXT) 可変構造。パースは router の責務。
screening_filters = Table(
    "screening_filters",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String, nullable=False),
    Column("criteria_json", String, nullable=False),  # JSON 文字列
    Column("created_at", String),  # ISO8601
    Column("updated_at", String),  # ISO8601
)

# ===== Phase 4: Stock Dossier（phase4-spec.md §2・ADR-020・0008_dossier） =====

# watchlist（夜の巡回対象・「最終調査日」の起点＝phase4-spec §2.1）。
# 自分データ（手入力で監視銘柄を選ぶ）なので code → stocks.code に FK を張る（裁定 L-7）。
# UNIQUE(code) を UPSERT/重複監視防止キーにする。
# last_investigated_at は列として持たない（調査側の真実 stock_dossiers を JOIN して一覧に出す）。
# stale 判定（21 日超）は backend が現在日から算出する（列に持たない・L-22）。
watchlist = Table(
    "watchlist",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("code", String, ForeignKey("stocks.code"), nullable=False),
    Column("note", String),  # メモ（任意）
    Column("added_at", String),  # 追加時刻 ISO8601
    UniqueConstraint("code", name="uq_watchlist_code"),  # 重複監視防止（UPSERT キー）
    Index("ix_watchlist_code", "code"),
)

# stock_dossiers（1 銘柄 1 行・living document＝phase4-spec §2.2・ADR-020）。
# AI 生成の調査要約（summary_md）をずっと更新し続ける。数値は Tool の事実に紐づく（ADR-014）。
# key_facts は JSON 文字列（PER/成長率/直近トピック等・SQLite に JSON 型なし・既存方針）。
stock_dossiers = Table(
    "stock_dossiers",
    metadata,
    Column("code", String, ForeignKey("stocks.code"), primary_key=True),  # 1 銘柄 1 行
    Column("summary_md", String),  # AI 生成の調査要約（markdown・living document）
    Column("key_facts", String),  # JSON 文字列（出所は Tool の事実）
    Column(
        "last_investigated_at", String
    ),  # 最終調査時刻 ISO8601（一覧の「最終調査日」・stale 起点）
    Column("updated_at", String),  # 行更新時刻 ISO8601
)

# dossier_sources（ソース台帳・本文非保存＝phase4-spec §2.3・ADR-020）。
# 取得 → 要約 → 本文は捨て、summary と url だけ残す（ストレージ・著作権の両面で全文保持は不採用）。
# UNIQUE(url) で再調査の二重取り込みを防ぐ（存在確認・冪等 UPSERT のキー）。
# 銘柄 FK（この銘柄のソース一覧）。code への索引で銘柄詳細の一覧取得を速くする。
dossier_sources = Table(
    "dossier_sources",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("code", String, ForeignKey("stocks.code"), nullable=False),
    Column("source_type", String),  # 'news'/'disclosure'/'twitter' 等（将来拡張）
    Column("url", String, nullable=False),  # 取り込み元 URL（本文は保存しない）
    Column("title", String),
    Column("summary", String),  # 短い要約（記事全文は捨てる＝ADR-020）
    Column("published_at", String),  # 発行日 'YYYY-MM-DD'（発行 1 週間以内のみ取り込む）
    Column("processed_at", String),  # 取り込み・要約した時刻 ISO8601
    UniqueConstraint("url", name="uq_dossier_sources_url"),  # URL 重複排除
    Index("ix_dossier_sources_code", "code"),
)
