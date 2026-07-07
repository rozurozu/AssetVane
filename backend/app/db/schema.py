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
    LargeBinary,
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
    # EDINET DB（edinetdb.jp）の銘柄キー（例 'E03006'）。#2 売掛/在庫の質の財務取得に使う
    # （ADR-064・夜間に sec_code↔edinet_code を /companies 一覧から解決して焼く）。未解決は None。
    Column("edinet_code", String),  # 第三者サービス edinetdb.jp の銘柄コード（ADR-064）
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
    # 直近の取得試行の成否（1=成功 / 0=失敗 / NULL=未試行）。fetch_index が試行ごとに記録し、
    # notify_digest が「今回取れなかった指数」を朝の digest に情報行で出すのに使う（ADR-018）。
    Column("last_attempt_ok", Integer),
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
# 保有評価額（遅延株価）＋ 現金 ＋ 外部資産 ＋ 投信評価額の合計（spec §3.3・ADR-054）。
asset_snapshots = Table(
    "asset_snapshots",
    metadata,
    Column("date", String, primary_key=True),  # 'YYYY-MM-DD'
    Column("total_value", Float),
    Column("stock_value", Float),
    Column("cash_value", Float),
    Column("external_value", Float),
    Column("fund_value", Float),  # 投信評価額合計（ADR-054・0015_funds で追加）
    Column("us_stock_value", Float),  # 米株評価額合計（JPY 換算後・ADR-057・0019_us_holdings_fx）
    Column("pnl", Float),
)

# ===== 投資信託（ADR-054: 専用テーブル・投信総合検索ライブラリー CSV で NAV 取得） =====
# 当初方針（投信は割合文脈で深追いしない）を ADR-054 で上書きし、非上場投信の保有を株と
# 同じ「取引→導出」構造で本格管理する。識別子は ISIN（NAV 取得が ISIN 必須）。基準価額・
# 取得単価・口数は日本の慣行「10,000 口あたりの円」で扱う（評価額 = units/10000 * nav）。
# external_assets には混ぜない（投信以外の不透明資産＝金・他口座まとめ専用に残す）。

# 投信マスタ（stocks 相当）。ユーザーが ISIN＋名称＋協会コードを 1 銘柄一度だけ登録する。
# 協会コード（0331418A 等）は NAV 取得に必須（投信総合検索ライブラリー CSV の associFundCd
# パラメータ。欠落すると空レスポンス＝実機確認 2026-06-08）。DB 列は nullable のままだが、
# 登録 API/UI の境界で必須にする（assoc_code 無しの投信は NAV 自動取得が効かず fetch_fund_navs
# が個別 skip する）。ISIN は NAV の isinCd と holdings 結合キーを兼ねる。
funds = Table(
    "funds",
    metadata,
    Column("isin", String, primary_key=True),  # 例 'JP90C000H1T1'（オルカン）
    Column("name", String, nullable=False),  # 表示名「eMAXIS Slim 全世界株式」等
    Column("assoc_code", String),  # 協会コード '0331418A'（NAV 取得に必須・登録境界で required）
    Column("updated_at", String),  # ISO8601
)

# NAV（基準価額）時系列（daily_quotes 相当・FundNavAdapter 供給）。
# (isin, date) を複合主キーにし UPSERT で冪等（ADR-002）。nav は 10,000 口あたりの円。
# isin への FK は張らない（生データ流儀＝index_quotes と同方針・マスタ未登録でも取り込める）。
fund_navs = Table(
    "fund_navs",
    metadata,
    Column("isin", String, nullable=False),
    Column("date", String, nullable=False),  # 基準日 'YYYY-MM-DD'
    Column("nav", Float),  # 基準価額（10,000 口あたりの円）
    PrimaryKeyConstraint("isin", "date", name="pk_fund_navs"),
    Index("ix_fund_navs_isin", "isin"),
    Index("ix_fund_navs_date", "date"),
)

# 投信取引記録（transactions 相当・一次データ。fund_holdings はここから導出＝ADR-019/054）。
# 自分データ（手入力）なので isin → funds.isin に FK を張る（誤入力防止・裁定 L-7）。
# price は約定時の基準価額（10,000 口あたりの円）、units は口数。毎月積立も buy として手入力。
fund_transactions = Table(
    "fund_transactions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("portfolio_id", Integer, ForeignKey("portfolios.portfolio_id"), nullable=False),
    Column("isin", String, ForeignKey("funds.isin"), nullable=False),
    Column("side", String, nullable=False),  # 'buy' / 'sell'
    Column("units", Float, nullable=False),  # 口数
    Column("price", Float, nullable=False),  # 約定基準価額（10,000 口あたりの円）
    Column("fee", Float),  # 手数料（任意・avg_cost には含めない）
    Column("traded_at", String, nullable=False),  # 約定日 'YYYY-MM-DD'
    Index("ix_fund_transactions_portfolio", "portfolio_id"),
    Index("ix_fund_transactions_isin", "isin"),
)

# 投信保有（holdings 相当・fund_transactions からの導出値。直接編集しない＝ADR-019/054）。
# (portfolio_id, isin) に UNIQUE を張り UPSERT キーとする。avg_cost は移動平均取得単価。
fund_holdings = Table(
    "fund_holdings",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("portfolio_id", Integer, ForeignKey("portfolios.portfolio_id"), nullable=False),
    Column("isin", String, ForeignKey("funds.isin"), nullable=False),
    Column("units", Float, nullable=False),  # 導出: Σbuy.units − Σsell.units
    Column("avg_cost", Float),  # 導出: 移動平均取得単価（10,000 口あたりの円）
    UniqueConstraint("portfolio_id", "isin", name="uq_fund_holdings_portfolio_isin"),
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
    # 会社予想（ガイダンス）。各四半期開示行に当期FY予想が standing で載る。FY実績行では空＝None
    # （実機確認 2026-06-30・ADR-063 #4）。beat/miss と上方/下方修正の素（解釈は LLM・ADR-014）。
    Column("forecast_net_sales", Float),  # 当期FY予想 売上高（FSales）
    Column("forecast_operating_profit", Float),  # 当期FY予想 営業利益（FOP）
    Column("forecast_profit", Float),  # 当期FY予想 純利益（FNP）
    Column("forecast_eps", Float),  # 当期FY予想 EPS（FEPS）
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

# 投資家プロファイル（ADR-082: テーマ C・★4 自己改善ループ）。policy（規範＝どうすべきか）と
# 厳格分離した「記述＝投資家の行動の癖（誰か）」の層。single row を育てる（id 固定・版管理機構なし
# ＝ADR-013 と同流儀だが policy とは別テーブル）。body は散文 1 枚。夜間バッチ profiler 面が台帳から
# 蒸留した傾向メモを承認制（proposals kind='profile_note'）で起票し、人間承認で body に追記される
# （ADR-009）。注入は CORE→POLICY に続く第 3 層（鏡・反追従で注入）。knowledge_cards とは別物。
investor_profile = Table(
    "investor_profile",
    metadata,
    Column("id", Integer, primary_key=True),  # 1 行運用（id 固定・autoincrement しない）
    Column("body", String, nullable=False, server_default=""),  # 散文プロファイル（空なら未育成）
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
    Column("llm_model", String),  # 監査用（面別に解決された実 model・ADR-058）
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
    # policy_change/buy/sell/rebalance/card_weight/profile_note（自由 String・CHECK なし）
    Column("kind", String, nullable=False),
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
    # ファンダ指標（ADR-048・0012_valuation_metrics）。当期＝最新FY、YoY は前期FYと突合。
    Column("roe", Float),  # eps / bps（純利益/自己資本・0..1）
    Column("operating_margin", Float),  # 営業利益 / 売上高（0..1）
    Column("net_margin", Float),  # 純利益 / 売上高（0..1）
    Column("revenue_growth_yoy", Float),  # 売上高 YoY 成長率（0..1 基準の比率）
    Column("op_growth_yoy", Float),  # 営業利益 YoY 成長率
    Column("profit_growth_yoy", Float),  # 純利益 YoY 成長率
    Column("eps_growth_yoy", Float),  # EPS YoY 成長率（FY 基準）
    # 会社予想（ガイダンス）の質シグナル（ADR-063 #4・夜間 calc_valuation が焼く事実）。
    # achievement=最新完了FY 実績÷その期の最終予想（beat/miss）、revision=進行中FY予想の直近
    # 上方/下方修正（最新Q÷前Q-1）。予想なし/赤字予想は None（捏造しない・ADR-014）。
    Column("op_forecast_achievement", Float),  # 営業利益 達成率（actual/forecast）
    Column("profit_forecast_achievement", Float),  # 純利益 達成率
    Column("op_forecast_revision", Float),  # 営業利益予想 直近修正（+=上方）
    Column("profit_forecast_revision", Float),  # 純利益予想 直近修正
    # 売掛/在庫の質シグナル（ADR-064 #2）。JP は edinetdb.jp の構造化財務（trade_receivables/
    # inventories/revenue/gross_profit）から夜間に焼く事実。回転日数は水準、YoY は伸び。
    # 「対売上の乖離（受取債権/在庫が売上より速く伸びていないか）」の解釈は revenue_growth_yoy と
    # 突き合わせて LLM が行う（捏造しない・分母0以下は None・ADR-014）。
    Column("receivables_turnover_days", Float),  # DSO=受取債権/売上×365（売掛金回転日数）
    Column("inventory_turnover_days", Float),  # DIO=在庫/売上原価×365（在庫回転日数）
    Column("receivables_growth_yoy", Float),  # 受取債権 YoY（同源 trade_receivables の前年比）
    Column("inventory_growth_yoy", Float),  # 棚卸資産 YoY（同源 inventories の前年比）
    # 清原式ネットキャッシュ（ADR-079）。net_cash=流動資産+投資有価証券×0.7−総負債（BS 由来の絶対
    # 額・JP も edinetdb.jp の investment_securities でフル式＝ADR-079 追補・欠落時のみ簡略式）。
    # 比率（÷時価総額）は物理列にせず read-time 導出（時価総額は日次・本列は四半期・鮮度バグ回避）。
    Column("net_cash", Float),  # 清原式ネットキャッシュ（絶対額・負値=実質ネット負債）
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
    Column("interval_days", Integer),  # 銘柄ごとの調査間隔（既定 21・stale 起点・ADR-033）
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

# ===== Phase 6: Signal Beacon（phase6-spec.md §2・ADR-007/018・0010_notifications） =====

# 通知の送信済み記録＝二重送信防止（ADR-002/018）。cron の coalesce 漏れや POST /batch/run の
# 手動再実行で run_nightly が同日 2 回走っても、同じ notify_key が既存なら 2 回目は送らない。
# notify_key は連番ではなく「種別:日付」の自然キー（再実行で同じ値になる＝冪等）。
# 本 Phase は digest 1 通に束ねる方針なので種別は `digest:<date>`・失敗は `error:<job>:<date>`。
# channel は将来の多チャンネル余地（当面 'discord' のみ＝ADR-007）。
notifications = Table(
    "notifications",
    metadata,
    Column("notify_key", String, primary_key=True),  # '種別:日付' の自然キー
    Column("channel", String, primary_key=True),  # 'discord'
    Column("sent_at", String),  # 送信時刻 ISO8601 UTC
)

# ===== ADR-044: ニュース統合コーパス（旧 general_news ＋ dossier_sources を統合・0013） =====

# ADR-044（ニュースを統合コーパスと階層タグに集約する）。旧 2 系統＝銘柄ニュース dossier_sources
# （ADR-020・code FK 必須）と一般ニュース general_news（ADR-034・category 列）を 1 本に統合し、
# 記事ごとに level（stock/sector/market/user）・code・sector17_code・category・source の階層タグを
# 持たせる。本文は保存せず summary と url のみ（ADR-020 堅持）。UNIQUE(url) ＋ 冪等 UPSERT で
# 再取得の二重取り込みを防ぐ。level/code/sector17_code に索引を張り、3 層（銘柄/セクター/市況）の
# タグフィルタ取り出し（get_news_context）を速くする。旧 source_type 列は source に改名した。
news = Table(
    "news",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("level", String, nullable=False),  # 'stock'/'sector'/'market'/'user' の階層タグ
    Column("code", String, ForeignKey("stocks.code")),  # stock 層の銘柄 FK（他層は NULL）
    # sector 層の J-Quants S17 業種コード '1'..'17'（stocks.sector17_code と同体系・ADR-053）
    Column("sector17_code", String),
    Column("category", String),  # market 層の表示ラベル（市況/マクロ/世界情勢・他層は NULL）
    Column("source", String),  # 'news'/'user'/'disclosure'/'twitter' 等（旧 source_type を改名）
    Column("url", String, nullable=False),  # 取り込み元 URL（本文は保存しない）
    Column("title", String),
    Column("summary", String),  # 短い要約（記事全文は捨てる＝ADR-020）
    Column("published_at", String),  # 発行日 'YYYY-MM-DD'
    Column("fetched_at", String),  # 取り込み時刻 ISO8601 UTC
    # 取得レベル 'summarized'/'description'/'headline'（本文取得の成否・ADR-020）
    Column("extraction_status", String),
    # ADR-045（ニュース意味検索 段階A）。意味検索用の埋め込みベクトル。格納は float32 LE の BLOB で
    # vec_distance_cosine が次元非依存にスキャンする（vec0 仮想表は使わない＝規模が育てば昇格）。
    Column("embedding", LargeBinary),  # float32 little-endian の BLOB（未埋め込み/機能オフは NULL）
    Column("embed_model", String),  # 埋め込みに使ったモデル名（不一致行を再埋め込み対象にするキー）
    Column("embedded_at", String),  # 埋め込み時刻 ISO8601 UTC
    # ADR-049/051（定性 polarity・能動配信）。stock 層ニュースの好/悪/中立の定性分類。
    # 'positive'/'negative'/'neutral'（NULL=未判定）。tag_news_polarity が stock 層のみ判定し
    # （他層は NULL のまま）、notify_digest の②保有銘柄悪材料アラートが polarity='negative' を拾う。
    # 数値 sentiment_score は持たない（AI に数値を作らせない＝ADR-014/049）。
    Column("polarity", String),
    UniqueConstraint("url", name="uq_news_url"),  # URL 重複排除（冪等 UPSERT のキー）
    Index("ix_news_level", "level"),  # 階層タグ別の取り出しを速くする
    Index("ix_news_code", "code"),  # 銘柄層（get_news_context の (i)）
    Index("ix_news_sector17", "sector17_code"),  # セクター層（get_news_context の (ii)）
)

# ===== Phase 7(B-1): 米国株スクリーナー（提示専用・ADR-031/039/048/055・0017_us_equity） =====
# 米株は日本株コア（stocks/daily_quotes/valuation_snapshots）と**物理的に別テーブル**で持つ
# （ADR-031 市場分離）。JPY 単一前提の資産評価コア（holdings/cash/asset_snapshots/portfolio
# metrics）には一切触れず、提示専用に閉じる（FX 換算・保有登録は Phase 7(B-2) 送り＝currency 列も
# 持たない）。データ源は yfinance 一本＝UsEquityAdapter（ADR-039(B)）。業種は Yahoo `.info.sector`
# （GICS 相当 11 分類の英語ラベル）を gics_sector 文字列で保持し industry を補助に持つ（ADR-055）。
# valuation 派生比率は日本株と同じ quant 純関数で読み取り時に Python 計算する（AI に計算させない
# ＝ADR-014/016）。

# 米株マスタ（stocks 相当）。ユニバースは NASDAQ Trader directory 由来＝当面普通株のみ巡回・
# is_etf フラグを持つ（ETF 拡張はフラグを外すだけ＝grill 確定）。財務素（eps/bps/株数/配当/
# 売上/営業利益/純利益）は yfinance `.info` を低頻度ローテ巡回（ADR-033 同型）で焼く。
# 業種/名称はここに持つ（日本株は stocks JOIN で補うが、米株は別系統で stocks に存在しない）。
us_stocks = Table(
    "us_stocks",
    metadata,
    Column("symbol", String, primary_key=True),  # 例 'AAPL'（NASDAQ Trader/yfinance のティッカー）
    Column("company_name", String),
    Column("gics_sector", String),  # Yahoo `.info.sector`（GICS 相当 11 分類・英語ラベル）
    Column("industry", String),  # Yahoo `.info.industry`（補助・細分類）
    Column("is_etf", Integer),  # ETF 判別フラグ（0/1・NASDAQ Trader の ETF 列由来）
    # 財務素（valuation 派生の素・読み取り時 Python 計算用＝ADR-014/048）。yfinance `.info` 由来。
    Column("eps", Float),  # EPS（PER = close / eps の素・`.info.trailingEps`）
    Column("bps", Float),  # BPS（PBR = close / bps の素・`.info.bookValue`）
    Column("shares_net", Float),  # 発行済株式数（時価総額の素・`.info.sharesOutstanding`）
    Column("dividend_per_share", Float),  # 年間配当（利回りの素・`.info.dividendRate`）
    Column("net_sales", Float),  # 売上高（利益率の素・`.info.totalRevenue`）
    Column("operating_profit", Float),  # 営業利益（営業利益率の素・近似＝adapter docstring 参照）
    Column("profit", Float),  # 純利益（純利益率の素・`.info.netIncomeToCommon`）
    # YoY 中継列（ADR-055）。yfinance `.info` 提供の YoY 率の中継器・実値（捏造ではない）。
    # `.info` は前期 FY 値を持たないため growth_yoy 純関数の素にはできないが、`.info` 自身が提供する
    # 率（revenueGrowth/earningsGrowth）はそのまま実値として us_valuation_snapshots へ転記する。
    Column("revenue_growth_yoy", Float),  # 売上 YoY（`.info.revenueGrowth`・実値）の中継
    Column("earnings_growth_yoy", Float),  # 純利益 YoY（`.info.earningsGrowth`・実値）の中継
    Column("fin_disclosed_date", String),  # 採用財務の基準日（監査・どの時点の `.info` か）
    Column("updated_at", String),  # 取得日時（ISO8601 文字列）
)

# 米株日足四本値（daily_quotes 相当・チャート用＝全履歴）。最大行数になるテーブル。
# (symbol, date) を複合主キーにし UPSERT で冪等（ADR-002）。FK は張らない（生データ流儀＝
# daily_quotes/index_quotes と同方針・マスタ未登録でも取り込める）。
us_daily_quotes = Table(
    "us_daily_quotes",
    metadata,
    Column("symbol", String, nullable=False),
    Column("date", String, nullable=False),  # 営業日 'YYYY-MM-DD'
    Column("open", Float),
    Column("high", Float),
    Column("low", Float),
    Column("close", Float),
    Column("volume", Float),
    Column("adj_close", Float),  # 調整後終値（配当・分割調整）
    PrimaryKeyConstraint("symbol", "date", name="pk_us_daily_quotes"),
    Index("ix_us_daily_quotes_symbol", "symbol"),
    Index("ix_us_daily_quotes_date", "date"),
)

# 米株バリュエーション・スナップショット（valuation_snapshots 相当・1 銘柄最新 1 行）。
# 派生比率（per/pbr/market_cap/dividend_yield/roe/各 margin/各 YoY）＋根拠の素（close/eps/bps/
# 配当/株数）を持つ。symbol PK＋FK→us_stocks.symbol（自分データ＝マスタ済み銘柄のみ焼く）。
# 列は valuation_snapshots（L364-388）を踏襲。ウェーブ1では書き込まない（calc はウェーブ2）。
us_valuation_snapshots = Table(
    "us_valuation_snapshots",
    metadata,
    Column("symbol", String, ForeignKey("us_stocks.symbol"), primary_key=True),
    Column("as_of_date", String, nullable=False),  # 採用した株価の営業日 'YYYY-MM-DD'
    Column("close", Float),  # 採用終値（adj 前の素の終値）
    Column("eps", Float),  # 採用財務の EPS
    Column("bps", Float),  # 採用財務の BPS
    Column("dividend_per_share", Float),  # 採用財務の年間配当
    Column("shares_net", Float),  # 発行済株式数（時価総額の素）
    Column("per", Float),  # close / eps
    Column("pbr", Float),  # close / bps
    Column("market_cap", Float),  # close * shares_net
    Column("dividend_yield", Float),  # dividend_per_share / close（0..1）
    Column("roe", Float),  # eps / bps（純利益/自己資本・0..1）
    Column("operating_margin", Float),  # 営業利益 / 売上高（0..1）
    Column("net_margin", Float),  # 純利益 / 売上高（0..1）
    Column("revenue_growth_yoy", Float),  # 売上高 YoY 成長率（0..1 基準の比率）
    Column("op_growth_yoy", Float),  # 営業利益 YoY 成長率
    Column("profit_growth_yoy", Float),  # 純利益 YoY 成長率
    Column("eps_growth_yoy", Float),  # EPS YoY 成長率
    # 売掛/在庫の質シグナル（ADR-064 #2・US 側）。yfinance balance_sheet（Receivables/Inventory）＋
    # income_stmt（Total Revenue/Cost Of Revenue）から焼く。JP の valuation_snapshots と対称。
    Column("receivables_turnover_days", Float),  # DSO=受取債権/売上×365
    Column("inventory_turnover_days", Float),  # DIO=在庫/売上原価×365
    Column("receivables_growth_yoy", Float),  # 受取債権 YoY
    Column("inventory_growth_yoy", Float),  # 棚卸資産 YoY
    # 清原式ネットキャッシュ（ADR-079・US 側）。US はフル式（投資有価証券×0.7 込み＝yfinance
    # Investments And Advances）。比率は JP と同じく read-time 導出（物理列にしない）。
    Column("net_cash", Float),  # 清原式ネットキャッシュ（絶対額・負値=実質ネット負債）
    Column("fin_disclosed_date", String),  # 採用した財務の基準日（監査）
    Column("updated_at", String),  # ISO8601（この行を焼いた時刻）
)

# ===== FX レート・米株保有管理（ADR-057・0019_us_holdings_fx・Phase 7(B-2)） =====
# Phase 7(B-1) で米株を提示専用に持ったが、JPY 単一前提の資産評価コアには触れなかった。本フェーズで
# (a) FX 基盤 (b) 米株保有管理 (c) 資産概要合算 を足す。市場分離（ADR-031）は維持＝米株は別テーブル
# で持ち、合算は資産概要レイヤの FX 換算でのみ行う（AI に計算させない・quant は通貨非依存＝
# ADR-014/016）。

# FX 日足終値（FxAdapter＝yfinance JPY=X 供給）。(date,pair) を複合主キーにし UPSERT で冪等
# （ADR-002）。rate は 1 USD あたりの JPY（JPY=X 終値）。FK は張らない（生データ流儀＝index_quotes/
# fund_navs 同方針）。
fx_rates = Table(
    "fx_rates",
    metadata,
    Column("date", String, nullable=False),  # 営業日 'YYYY-MM-DD'
    Column("pair", String, nullable=False),  # 通貨ペア 'USDJPY'
    Column("rate", Float),  # 1 USD あたりの JPY（JPY=X 終値）
    PrimaryKeyConstraint("date", "pair", name="pk_fx_rates"),
    Index("ix_fx_rates_pair", "pair"),
)

# 米株取引記録（transactions 相当・一次データ。us_holdings はここから導出＝ADR-019/057）。
# 自分データ（手入力）なので symbol → us_stocks.symbol に FK を張る（誤入力防止・裁定 L-7）。
# price は約定単価（USD）。fx_rate は約定時 USDJPY（JPY/USD）で、取得時レートを記録して原価を JPY
# 固定する（評価額は現レート → 為替損益が含み損益に乗る厳密含み損益＝ADR-057）。単一ユーザー
# （ADR-001）ゆえ portfolio_id は持たない（global 保有）。
us_transactions = Table(
    "us_transactions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("symbol", String, ForeignKey("us_stocks.symbol"), nullable=False),
    Column("side", String, nullable=False),  # 'buy' / 'sell'
    Column("shares", Float, nullable=False),
    Column("price", Float, nullable=False),  # 約定単価（USD）
    Column("fee", Float),  # 手数料（USD・任意・avg_cost には含めない）
    Column("traded_at", String, nullable=False),  # 約定日 'YYYY-MM-DD'
    Column("fx_rate", Float, nullable=False),  # 約定時 USDJPY（JPY/USD・取得時レート記録）
    Column("note", String),  # 任意メモ
    Index("ix_us_transactions_symbol", "symbol"),
)

# 米株保有（holdings 相当・us_transactions からの導出値。直接編集しない＝ADR-019/057）。
# symbol に UNIQUE を張り UPSERT キーとする（単一ユーザー・global 保有）。avg_cost は USD 建て移動
# 平均、avg_cost_jpy は取得時レートで JPY 固定した移動平均原価（為替損益を含み損益に乗せる素）。
us_holdings = Table(
    "us_holdings",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("symbol", String, ForeignKey("us_stocks.symbol"), nullable=False),
    Column("shares", Float, nullable=False),  # 導出: Σbuy.shares − Σsell.shares
    Column("avg_cost", Float),  # 導出: 移動平均取得単価（USD）
    Column("avg_cost_jpy", Float),  # 導出: 取得時レートで JPY 固定した移動平均原価
    UniqueConstraint("symbol", name="uq_us_holdings_symbol"),
)

# ===== テーマタグ（ADR-050 改訂・ADR-056・0018_themes・data-model.md「テーマタグ」節） =====
# 業種コードをまたぐテーマ（"AI需要"・"防衛"・"円安メリット" 等）で銘柄を束ねる。全ユニバース
# （JP＋US）を実在テキスト（米株 longBusinessSummary・JP は EDINET 有報「事業の内容」）に grounded
# で事前タグ付けする（名前推測禁止）。テーマは定性タグで数値ではない（ADR-014）。

# テーマ語彙の目録（JP＋US 横断のグローバル語彙・"AI需要" は市場を跨いで 1 語）。
# 語彙は単調増加で消さない（reconcile の資産）。embedding は語彙 reconcile 用（ADR-045 の
# vec_distance_cosine 流用・float32 LE・未埋め込み/機能オフは NULL）。near_duplicate_of は
# 近接した既存テーマ名の重複候補フラグ（自動マージはせず候補提示のみ）。
themes = Table(
    "themes",
    metadata,
    Column("name", String, primary_key=True),  # canonical なテーマ名
    Column("embedding", LargeBinary),  # 語彙 reconcile 用ベクトル（float32 LE・NULL 可）
    Column("embed_model", String),  # 埋め込みに使ったモデル名（差替検知・NULL 可）
    Column("first_seen_at", String),  # 初出日時 ISO8601
    Column("near_duplicate_of", String),  # 近接既存テーマ名（重複候補・NULL 可）
)

# 銘柄×theme 台帳（JP＋US 横断）。code への cross-FK は張らない（signals と同じ生データ流儀・
# US は別テーブルのため）。source 列も持たない＝書き込みは UPSERT＋last_seen_at bump（削除しない）、
# 古いタグは時間窓 prune で枯らす。これでユニバースタガーと investigate オーバーレイの 2 書き手が
# クロバーせず共存する（ADR-050 の三択トレードオフ解・意図的決定）。
stock_themes = Table(
    "stock_themes",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("market", String, nullable=False),  # 'JP'/'US'
    Column("code", String, nullable=False),  # JP 5桁コード or US symbol
    Column("theme_name", String, nullable=False),  # themes.name（canonical 名のみ）
    Column("first_assigned_at", String),  # 初付与日時 ISO8601
    Column("last_seen_at", String),  # 最終再確認日時 ISO8601（時間窓 prune の基準）
    UniqueConstraint("market", "code", "theme_name", name="uq_stock_themes_market_code_theme"),
    Index("ix_stock_themes_market_code", "market", "code"),  # 銘柄のテーマ一覧
    Index("ix_stock_themes_theme_name", "theme_name"),  # テーマ株スクリーニング
)

# 事業説明の実在テキスト（市場横断・grounded タガーの信号源・ADR-050/056）。
# description_text は compact プロフィール（JP は EDINET「事業の内容」を要約・US は
# longBusinessSummary を素のまま・本文は持たない＝ADR-020）。source/doc_id/disclosed_date は
# テキストの provenance（タグの provenance ではない＝stock_themes とは役割が別）。
# fetched_at は「テキスト最終変化時刻」（同一テキストの再 UPSERT では更新しない＝repo 契約）で、
# 差分タガーが「変化した銘柄」だけ再タグする判定材料になる。
company_descriptions = Table(
    "company_descriptions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("market", String, nullable=False),  # 'JP'/'US'
    Column("code", String, nullable=False),  # JP 5桁 or US symbol（cross-FK なし）
    Column("source", String),  # 'edinet'(JP有報) / 'dossier'(JP調査W2) / 'yfinance'(US説明)
    Column("description_text", String),  # compact プロフィール（本文は持たない）
    Column("disclosed_date", String),  # テキストの基準日（EDINET 提出/開示日・US は NULL）
    Column("doc_id", String),  # EDINET 書類管理番号（provenance・US は NULL）
    Column("fetched_at", String),  # テキスト最終変化時刻 ISO8601（時刻まで）
    UniqueConstraint("market", "code", name="uq_company_descriptions_market_code"),
)

# ===== 訂正有報フラグ（B-2・docTypeCode=130・0027_edinet_restatements） =====
# EDINET 提出日クロール（fetch_edinet_descriptions）が「捨てていた」訂正有価証券報告書
# （docTypeCode='130'）の出現を、本文を取らず一覧の事実だけ記録する append-only 台帳。
# 訂正の有無は会計・開示の品質シグナル（earnings quality）で、get_valuation が
# last_restatement_at（最新訂正の提出日）として中継する。recency（「直近か」）は数値でなく
# 解釈なので LLM に委ねる（事実=日付のみ持つ・ADR-014）。冪等キーは doc_id（再クロール安全）。
edinet_restatements = Table(
    "edinet_restatements",
    metadata,
    Column("doc_id", String, primary_key=True),  # EDINET 書類管理番号（冪等キー）
    Column("code", String, nullable=False),  # JP 5桁（secCode・cross-FK なし）
    Column("disclosed_date", String, nullable=False),  # 訂正の提出日 'YYYY-MM-DD'（クロール日）
    Column("filer_name", String),  # 提出者名（provenance・任意）
    Column("doc_type_code", String),  # '130'（訂正有報・将来の派生コードにも備え保持）
    Column("created_at", String),  # この行を記録した時刻 ISO8601
    Index("ix_edinet_restatements_code", "code"),
)

# ===== ADR-058: LLM プロバイダ複数登録・面別 provider/model 設定（0022_llm_providers） =====
#
# LLM 接続の正本を env（起動時固定）から DB へ移し、/settings の WebUI から複数 provider を
# 登録し、面（chat/nightly/dossier/tagger）ごとに provider と model を割り当てられるようにする
# （ADR-058・ADR-012 の延長）。OpenAI 互換 1 本で全 provider を吸収する
# （codex 経路は ADR-073 で撤去）。

# 鍵あり provider のレジストリ（OpenAI 互換 {base_url, api_key, model} で全部吸収・ADR-012）。
# api_key は平文（ADR-001 単一ユーザー・認証なし・LAN 内が前提。将来は暗号化＝ADR-058 に明記）。
llm_providers = Table(
    "llm_providers",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String, nullable=False),  # UI 表示名（一意・"OpenRouter" 等）
    Column("base_url", String, nullable=False),  # OpenAI 互換 /v1（例 https://api.openai.com/v1）
    Column("api_key", String, nullable=False, server_default=""),  # 平文。空可（ローカル LLM）
    Column(
        "default_model", String, nullable=False, server_default=""
    ),  # 面が空のとき使う既定 model
    Column("created_at", String),  # ISO8601
    Column("updated_at", String),  # ISO8601
    UniqueConstraint("name", name="uq_llm_providers_name"),
)

# 面（face）→ {provider, model} の割当（chat/nightly/dossier/tagger の 4 行運用・ADR-058 確定3）。
# provider_id は NULL=未設定 / >0=llm_providers.id。FK は張らない
# （SQLite で FK 無効運用・整合は services/router が守る＝ADR-058 確定7）。model は自由入力文字列
# （v1 は reasoning_effort 等の細目を持たない＝確定5）。シードなし（行が無い面＝未設定＝確定4）。
llm_face_config = Table(
    "llm_face_config",
    metadata,
    Column("face", String, primary_key=True),  # 'chat'/'nightly'/'dossier'/'tagger'
    Column("provider_id", Integer),  # NULL=未設定 / >0=llm_providers.id
    Column("model", String, nullable=False, server_default=""),  # 自由入力（空なら provider 既定）
    # 推論努力（ADR-059・0023）。空=既定（openai は送らない）。
    # 値域は minimal/low/medium/high（openai→chat.completions）。
    Column("reasoning_effort", String),
    Column("updated_at", String),  # ISO8601
)

# 意味検索の embedding 接続（ADR-059・0023・単一行運用＝policy 同型）。env から DB へ移管。
# chat provider とは独立（/v1/embeddings・別 model・別キーが普通）。api_key は平文（ADR-058 同方針・
# GET ではマスク）。3 キー（base_url/api_key/model）が揃って初めて有効＝欠ければ静かに機能オフ
# （ADR-006/045）。dim は次元非依存格納のため任意（検証/ログ用）。timeout は env 据え置き。
embedding_config = Table(
    "embedding_config",
    metadata,
    Column("id", Integer, primary_key=True),  # 1 行運用（id 固定）
    Column("base_url", String, nullable=False, server_default=""),  # OpenAI 互換 /v1
    Column("api_key", String, nullable=False, server_default=""),  # 平文・GET でマスク
    Column("model", String, nullable=False, server_default=""),  # 例 text-embedding-3-small
    Column("dim", Integer),  # 任意（0/NULL=未設定）
    Column("updated_at", String),  # ISO8601
)

# J-Quants V2 接続（ADR-061・0024_jquants_config・単一行運用＝embedding_config 同型）。
# api_key とプラン名を env から DB へ移管し、/settings の WebUI から編集する（ADR-058/059）。
# api_key は平文（ADR-001 単一ユーザー・LAN 内前提・GET ではマスク）。plan は契約プラン名
# （free/light/standard/premium）で、スロットル間隔（秒）は adapters/jquants.py の _PLAN_INTERVALS
# がプラン名から決める（ADR-008・秒数を DB に持たない）。env シードはしない＝初回は未登録（鍵空）で
# 設定するまでバッチは JQuantsError で落ちる（LLM 面未設定と同じ割り切り・ADR-018）。
jquants_config = Table(
    "jquants_config",
    metadata,
    Column("id", Integer, primary_key=True),  # 1 行運用（id 固定）
    Column("api_key", String, nullable=False, server_default=""),  # 平文・GET でマスク
    Column("plan", String, nullable=False, server_default="free"),  # free/light/standard/premium
    Column("updated_at", String),  # ISO8601
)

# EDINET DB（edinetdb.jp・第三者サービス）接続設定（ADR-064・0030_edinetdb_config）。
# 公式 EDINET（ADR-056/087・DB の edinet_config）とは別系統。#2 売掛/在庫の質の
# 構造化財務取得に使う。api_key/plan を env でなく DB+WebUI（/settings）
# で管理する＝jquants_config と
# 同型（ADR-061）。plan は free/pro（当面 free・pro 検討）。実レート予算は adapter が
# x-ratelimit-* ヘッダで把握する（plan 定数は throttle 間隔・夜間ソフト上限の目安・services 側）。
edinetdb_config = Table(
    "edinetdb_config",
    metadata,
    Column("id", Integer, primary_key=True),  # 1 行運用（id 固定）
    Column("api_key", String, nullable=False, server_default=""),  # 平文・GET でマスク
    Column("plan", String, nullable=False, server_default="free"),  # free/pro
    Column("updated_at", String),  # ISO8601
)

# 公式 EDINET（第三者 edinetdb.jp とは別）接続設定（ADR-087・0041_edinet_config）。Subscription-Key
# （api_key）を env（旧 edinet_api_key）から DB+WebUI（/settings）へ移す＝jquants/edinetdb_config
# 同型（ADR-061/064）。動機は「公式は env・edinetdb は DB」の非対称で実機がキーを貼り間違え、
# 公式 EDINET が拒否して夜バッチが停止したこと。plan 列は持たない（公式 EDINET は回数クォータ無し・
# レート制限のみ＝スロットル間隔等の非秘密つまみは config に残す）。api_key は平文（ADR-001・GET で
# マスク）。env シードはしない＝初回は未登録（鍵空）で段階C は静かに skip。
edinet_config = Table(
    "edinet_config",
    metadata,
    Column("id", Integer, primary_key=True),  # 1 行運用（id 固定）
    Column("api_key", String, nullable=False, server_default=""),  # 平文・GET でマスク
    Column("updated_at", String),  # ISO8601
)

# ===== ADR-062: 知識カード基盤（knowledge_cards・0025_knowledge_cards） =====
# AI アドバイザーの「③知識軸」を CORE（規律・不変・リポジトリ）/ POLICY（方針・可変・DB）に続く
# 第 3 の知識源として DB 化する。旧・手法カード（cards/*.md・全カード常時注入＝ADR-016/048）を廃し、
# 増える知識（市場文脈・外部メモ・手法の解釈）を 1 表に集約して UI 管理・RAG 取得する。
# data-model.md の将来予約 method_cards を実体化＋改名（"method" の 3 分裂＝手法/カード/カタログを
# 解消）。計算そのものは持たない（計算は必ずコード＝quant・ADR-014/016）。手法↔計算の索引は
# method_cards（advisor/method_cards/*.md）が signal_type キーで持つ（ADR-075・旧 linked_signal_type
# 列は 0035 で DROP）。embedding は when_to_apply の意味検索キー
# （ADR-045 同型・float32 LE BLOB を vec_distance_cosine が次元非依存にスキャン）。
knowledge_cards = Table(
    "knowledge_cards",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("title", String, nullable=False),  # 例「東証の低 PBR 是正」
    Column("body", String, nullable=False),  # 注入される知識本文（要約・散文）
    Column("when_to_apply", String),  # 適用条件＝retrieval キー（embedding 対象・ADR-045 同型）
    # ライフサイクル（ADR-062・AI 審査が初期値を付け、active 化は人間が最終承認＝ADR-009）:
    # 'draft'（入力直後）/ 'active'（注入対象）/ 'needs_quant'（計算未実装で実装待ち）/
    # 'to_core'（規律→CORE 誘導）/ 'rejected'（LLM 一般知識でカード不要）。
    Column("status", String, nullable=False, server_default="draft"),
    Column(
        "level", String
    ),  # 構造タグ 'stock'/'sector'/'market'/'general'（事前フィルタ・ADR-044 同体系）
    Column("sector17_code", String),  # 業種事前フィルタ（J-Quants S17・任意・ADR-053）
    Column("theme", String),  # テーマ事前フィルタ（任意）
    # 銘柄粒度の知識軸（ADR-062 追補・0033）。code あり＝level='stock' の 1 カード 1 銘柄。
    # market は 'JP'/'US'（同定は market+code）。注入は当面 code 一致
    # （FocusRef は market を運ばない）。
    # code 付きは exact-match でだけ注入し汎用の意味検索プールからは除外する（他銘柄漏れ防止）。
    Column("market", String),  # 'JP'/'US'（銘柄ノートのとき・非銘柄カードは NULL）
    Column("code", String),  # 銘柄コード（JP 5 桁 / US ティッカー・非銘柄カードは NULL）
    # 手法↔signal の索引は method_cards（advisor/method_cards/*.md）が signal_type キーで持つ
    # （ADR-075）。旧 linked_signal_type 列は冗長のため 0035 で DROP 済み。
    Column("quant_note", String),  # needs_quant のとき「必要な計算」のメモ
    Column(
        "always_inject", Integer, nullable=False, server_default="0"
    ),  # 1=常時注入の例外保険（0/1）
    # 重要度（ADR-062・0026）。retrieval/注入順を distance/weight で重み付け（重いほど上位）。
    # 古い/信頼度が下がったカードは weight を下げて生かす（created_at の鮮度と併せ AI が解釈）。
    Column("weight", Float, nullable=False, server_default="1.0"),
    Column("source", String),  # URL/引用/由来（YouTuber 動画 URL 等）
    # AI 審査（assist_card）の判定理由＝再読込後も「なぜ却下/採用候補か」が残る
    # （ADR-062 追補・0028・None=AI 未整形）。
    Column("triage_reason", String),
    # ADR-045 同型の埋め込み 3 列（when_to_apply のベクトル・未埋め込み/機能オフは NULL）。
    Column("embedding", LargeBinary),  # float32 LE BLOB
    Column("embed_model", String),  # 埋め込みモデル名（不一致行を再埋め込み対象にするキー）
    Column("embedded_at", String),  # 埋め込み時刻 ISO8601 UTC
    Column("created_at", String),  # ISO8601
    Column("updated_at", String),  # ISO8601
    Index("ix_knowledge_cards_status", "status"),  # active 行の取り出しを速くする
    # 銘柄ノートの exact-match 注入を速くする（ADR-062 追補・0033）。
    Index("ix_knowledge_cards_market_code", "market", "code"),
)

# ===== ADR-067: 夜 digest 注目シグナルの AI 選別（notable_picks・0032_notable_picks） =====
# 夜 digest の「注目シグナル」を score 閾値 Top N 抽出から「合流(confluence)ゲート＋AI 選別」へ
# 作り直す（ADR-067）。Python が独立材料 2 次元以上の重なりで候補集合を決定論的に組み、夜の分析AI が
# 総合的に注目すべき銘柄だけを submit_notable_stocks で選ぶ（ADR-014）。その選別結果をここに永続し、
# 後続の notify_digest が読んで digest 本文に載せる（journal/proposals と同じ「夜AIが書き digest が
# 読む」パターン）。source で 'nightly'（夜の自動選別）/ 'chat'（昼チャット）を区別し、digest は
# nightly を読む。UNIQUE(date,code,source) ＋ 冪等 UPSERT で再実行でも重複させない（ADR-002）。
# code への FK は張らない（signals と同じ生データ流儀・解決は persist 側が担う＝ADR-052 同型）。
notable_picks = Table(
    "notable_picks",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("date", String, nullable=False),  # 夜の UTC 日付 'YYYY-MM-DD'（journal と揃える）
    Column("code", String, nullable=False),  # JP 5 桁（候補は JP ユニバース・ADR-067）
    Column("reason", String),  # AI の選定理由（なぜ注目か・数値は Tool 事実由来＝ADR-014）
    Column("source", String, nullable=False, server_default="nightly"),  # 'nightly'/'chat'
    Column("created_at", String),  # ISO8601 UTC
    UniqueConstraint("date", "code", "source", name="uq_notable_picks_date_code_source"),
    Index("ix_notable_picks_date", "date"),
)

# ===== ADR-077: AI 過去提案の市場結果採点（proposal_outcomes・0036_proposal_outcomes） =====
# 夜の分析AI・チャットが出した buy/sell 提案（proposals・ADR-052）と注目選別（notable_picks・
# ADR-067）を、提案日の終値を起点に N 営業日後の実現（超過）リターンで事後採点する台帳（テーマ A）。
# 夜バッチ初の backward-looking ジョブ score_proposal_outcomes が quant/outcome.py の純関数で焼き、
# Tool get_track_record が集計を返す（AI は自分の成績を pull で確認・push しない＝ADR-014/025）。
# proposals.outcome（承認/却下の人手メモ）とは別列・別テーブルで「提示ベースの銘柄選択スキル評価」を
# 分離する（実 P/L ではない）。origin_id は proposals.id / notable_picks.id の参照だが FK は張らない
# （2 表参照・signals/notable_picks と同じ生データ流儀）。UNIQUE(origin_kind,origin_id,horizon)＋
# 冪等 UPSERT で再実行・pending→final の上書きに耐える（ADR-002）。
proposal_outcomes = Table(
    "proposal_outcomes",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("origin_kind", String, nullable=False),  # 'proposal'（buy/sell）/ 'notable'
    Column(
        "origin_id", Integer, nullable=False
    ),  # proposals.id / notable_picks.id（FK は張らない）
    Column(
        "source", String, nullable=False
    ),  # 'nightly'/'chat'（proposal は journal 由来・NULL→chat）
    Column("kind", String, nullable=False),  # 'buy'/'sell'/'notable'（notable は非方向＝hit なし）
    # 提案時の確信度 'high'/'medium'/'low'（body から非正規化・notable/legacy は NULL＝ADR-084）。
    # CHECK は張らない（kind/source/status 同様アプリ層で正規化＝house style・news.polarity 前例）。
    Column("conviction", String),
    Column("code", String, nullable=False),  # JP 5 桁 / US ティッカー
    Column("market", String, nullable=False),  # 'JP'/'US'（notable は常に JP）
    Column(
        "entry_date", String, nullable=False
    ),  # 起点日=proposals.created_date / notable_picks.date
    Column("horizon", Integer, nullable=False),  # 保有営業日数（20/60・系列 N 本先）
    Column("entry_priced_date", String),  # 実際に採用した起点バー日（forward で前進した場合ずれる）
    Column("entry_price", Float),  # 起点バーの adj_close
    Column("as_of_date", String),  # 到達バー日（pending は NULL）
    Column("exit_price", Float),  # 到達バーの adj_close
    Column("realized_return", Float),  # 絶対リターン（exit/entry - 1・pending は NULL）
    Column("benchmark_symbol", String),  # '^TPX'（JP）/ '^SPX'（US）
    Column("excess_return", Float),  # 対ベンチ超過（ベンチ欠測は NULL）
    Column("benchmark_fallback", Integer),  # 1=ベンチ欠測で hit を絶対リターンで判定した
    Column("hit", Integer),  # 1/0（buy: リターン>0・sell: リターン<0）／notable・pending は NULL
    Column("status", String, nullable=False, server_default="pending"),  # 'pending'/'final'
    Column("scored_at", String),  # ISO8601 UTC（最終採点時刻）
    UniqueConstraint(
        "origin_kind", "origin_id", "horizon", name="uq_proposal_outcomes_origin_horizon"
    ),
    Index("ix_proposal_outcomes_status", "status"),
    Index("ix_proposal_outcomes_agg", "source", "kind", "horizon"),
    Index("ix_proposal_outcomes_entry", "entry_date"),
)
