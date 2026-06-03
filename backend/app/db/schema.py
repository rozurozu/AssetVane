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
