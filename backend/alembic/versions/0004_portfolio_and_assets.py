"""portfolio_and_assets: ポートフォリオ・資産テーブル（Phase 2）

Revision ID: 0004_portfolio_and_assets
Revises: 0003_signals
Create Date: 2026-06-03

Phase 2（phase2-spec.md §2.1・ADR-001/002/019）。
portfolios / transactions / holdings / cash / external_assets /
index_quotes / asset_snapshots を作成。
自分データ（手入力）は FK を張る
（transactions・holdings の code→stocks.code・portfolio_id→portfolios）。
生データ間（index_quotes 等）は FK を張らない（既存流儀）。
portfolios に (portfolio_id=1, name='Default') の seed 行を投入する（spec §2）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_portfolio_and_assets"
down_revision: str | None = "0003_signals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # portfolios: ポートフォリオ器（ADR-001・spec §2.1）
    portfolios_table = op.create_table(
        "portfolios",
        sa.Column("portfolio_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("portfolio_id"),
    )

    # seed: portfolio_id=1 の Default ポートフォリオを 1 行挿入（spec §2）
    op.bulk_insert(
        portfolios_table,
        [
            {
                "portfolio_id": 1,
                "name": "Default",
                "created_at": "2026-06-03T00:00:00+00:00",
            }
        ],
    )

    # transactions: 取引記録（ADR-019: 一次データ。holdings はここから導出）
    # 自分データなので FK を張る（code→stocks.code・portfolio_id→portfolios）
    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("portfolio_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("side", sa.String(), nullable=False),  # 'buy' / 'sell'
        sa.Column("shares", sa.Float(), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),  # 約定単価
        sa.Column("fee", sa.Float(), nullable=True),  # 手数料（任意）
        sa.Column("traded_at", sa.String(), nullable=False),  # 約定日 'YYYY-MM-DD'
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.portfolio_id"]),
        sa.ForeignKeyConstraint(["code"], ["stocks.code"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_transactions_portfolio", "transactions", ["portfolio_id"])
    op.create_index("ix_transactions_code", "transactions", ["code"])

    # holdings: 保有銘柄（ADR-019: transactions からの導出値。直接編集しない）
    op.create_table(
        "holdings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("portfolio_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("shares", sa.Float(), nullable=False),  # 導出: Σbuy.shares − Σsell.shares
        sa.Column("avg_cost", sa.Float(), nullable=True),  # 導出: 移動平均取得単価
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.portfolio_id"]),
        sa.ForeignKeyConstraint(["code"], ["stocks.code"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("portfolio_id", "code", name="uq_holdings_portfolio_code"),
    )

    # cash: 投資用待機現金（JPY・通貨列は Phase 7 まで持たない）
    op.create_table(
        "cash",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("balance", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    # external_assets: 外部資産（投信・コモディティ等の手入力）
    op.create_table(
        "external_assets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(), nullable=False),  # 「オルカン」等
        sa.Column("category", sa.String(), nullable=True),  # 投信/コモディティ等
        sa.Column("value", sa.Float(), nullable=True),  # 評価額（手入力）
        sa.Column("proxy_symbol", sa.String(), nullable=True),  # 概算 proxy（指数等）
        sa.Column("monthly_contribution", sa.Float(), nullable=True),  # 毎月積立（任意）
        sa.Column("as_of", sa.String(), nullable=True),  # 基準日
        sa.PrimaryKeyConstraint("id"),
    )

    # index_quotes: 主要指数の水準（daily_quotes とは別粒度・別出所・IndexAdapter 供給）
    # FK は張らない（指数シンボルは stocks に存在しない・生データ流儀）
    op.create_table(
        "index_quotes",
        sa.Column("symbol", sa.String(), nullable=False),  # 'TOPIX' / '^GSPC' 等
        sa.Column("date", sa.String(), nullable=False),  # 'YYYY-MM-DD'
        sa.Column("close", sa.Float(), nullable=True),  # 終値（水準）
        sa.PrimaryKeyConstraint("symbol", "date", name="pk_index_quotes"),
    )
    op.create_index("ix_index_quotes_symbol", "index_quotes", ["symbol"])

    # asset_snapshots: 日次総資産（夜間バッチが焼く・1 日 1 行）
    op.create_table(
        "asset_snapshots",
        sa.Column("date", sa.String(), nullable=False),  # 'YYYY-MM-DD'
        sa.Column("total_value", sa.Float(), nullable=True),
        sa.Column("stock_value", sa.Float(), nullable=True),
        sa.Column("cash_value", sa.Float(), nullable=True),
        sa.Column("external_value", sa.Float(), nullable=True),
        sa.Column("pnl", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("date"),
    )


def downgrade() -> None:
    op.drop_table("asset_snapshots")
    op.drop_index("ix_index_quotes_symbol", table_name="index_quotes")
    op.drop_table("index_quotes")
    op.drop_table("external_assets")
    op.drop_table("cash")
    op.drop_table("holdings")
    op.drop_index("ix_transactions_code", table_name="transactions")
    op.drop_index("ix_transactions_portfolio", table_name="transactions")
    op.drop_table("transactions")
    op.drop_table("portfolios")
