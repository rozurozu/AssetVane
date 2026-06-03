"""financials: 財務・決算テーブル（Phase 2）

Revision ID: 0005_financials
Revises: 0004_portfolio_and_assets
Create Date: 2026-06-03

Phase 2（phase2-spec.md §2.1・data-model.md §2・ADR-002）。
financials テーブル＋ ix_financials_code インデックスを作成。
自分データ（保有銘柄の財務）なので code → stocks.code に FK を張る（裁定 L-7）。
V2 財務エンドポイント（/v2/fins/statements 等）と実フィールド名は未確定（jquants.md §6 要再確認）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_financials"
down_revision: str | None = "0004_portfolio_and_assets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # financials: 財務・決算（spec §2.1・data-model.md §2）
    # (code, disclosed_date, fiscal_period) を複合主キーにし UPSERT で冪等（ADR-002）。
    op.create_table(
        "financials",
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("disclosed_date", sa.String(), nullable=False),  # 開示日 'YYYY-MM-DD'
        sa.Column("fiscal_period", sa.String(), nullable=False),  # 例 '2025Q1' / 'FY2024'
        sa.Column("net_sales", sa.Float(), nullable=True),  # 売上高
        sa.Column("operating_profit", sa.Float(), nullable=True),  # 営業利益
        sa.Column("profit", sa.Float(), nullable=True),  # 純利益
        sa.Column("eps", sa.Float(), nullable=True),  # EPS
        sa.Column("bps", sa.Float(), nullable=True),  # BPS
        sa.ForeignKeyConstraint(["code"], ["stocks.code"]),
        sa.PrimaryKeyConstraint("code", "disclosed_date", "fiscal_period", name="pk_financials"),
    )
    op.create_index("ix_financials_code", "financials", ["code"])


def downgrade() -> None:
    op.drop_index("ix_financials_code", table_name="financials")
    op.drop_table("financials")
