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
