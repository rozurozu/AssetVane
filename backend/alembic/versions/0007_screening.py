"""screening: バリュエーション・スクリーニング基盤（ADR-031）

Revision ID: 0007_screening
Revises: 0006_advisor_state
Create Date: 2026-06-04

/stocks スクリーナー（PER/PBR/時価総額/配当利回り）の基盤。
1. financials に配当・株数カラムを追加（J-Quants summary: FDivAnn/DivAnn/ShOutFY/TrShFY）。
2. valuation_snapshots（1 銘柄最新 1 行・夜間ジョブ calc_valuation が焼く）を作成。
3. screening_filters（保存フィルタ・単一ユーザーなので user_id なし）を作成。
冪等性: 既存 DB への再適用に備え、列・テーブルの存在チェックをしてから追加する
（0001 を冪等化したのと同じ方針）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_screening"
down_revision: str | None = "0006_advisor_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _existing_columns(table: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {c["name"] for c in inspector.get_columns(table)}


def _existing_tables() -> set[str]:
    bind = op.get_bind()
    return set(sa.inspect(bind).get_table_names())


def upgrade() -> None:
    # 1. financials に配当・株数カラムを追加（既存列はスキップ＝冪等）
    cols = _existing_columns("financials")
    for name in ("dividend_per_share", "shares_outstanding", "treasury_shares"):
        if name not in cols:
            op.add_column("financials", sa.Column(name, sa.Float(), nullable=True))

    tables = _existing_tables()

    # 2. valuation_snapshots（夜間 calc_valuation が焼く・読み取り時に絞る）
    if "valuation_snapshots" not in tables:
        op.create_table(
            "valuation_snapshots",
            sa.Column("code", sa.String(), nullable=False),
            sa.Column("as_of_date", sa.String(), nullable=False),
            sa.Column("close", sa.Float(), nullable=True),
            sa.Column("eps", sa.Float(), nullable=True),
            sa.Column("bps", sa.Float(), nullable=True),
            sa.Column("dividend_per_share", sa.Float(), nullable=True),
            sa.Column("shares_net", sa.Float(), nullable=True),
            sa.Column("per", sa.Float(), nullable=True),
            sa.Column("pbr", sa.Float(), nullable=True),
            sa.Column("market_cap", sa.Float(), nullable=True),
            sa.Column("dividend_yield", sa.Float(), nullable=True),
            sa.Column("fin_disclosed_date", sa.String(), nullable=True),
            sa.Column("updated_at", sa.String(), nullable=True),
            sa.ForeignKeyConstraint(["code"], ["stocks.code"]),
            sa.PrimaryKeyConstraint("code", name="pk_valuation_snapshots"),
        )

    # 3. screening_filters（保存フィルタ・user_id なし＝ADR-001）
    if "screening_filters" not in tables:
        op.create_table(
            "screening_filters",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("criteria_json", sa.String(), nullable=False),
            sa.Column("created_at", sa.String(), nullable=True),
            sa.Column("updated_at", sa.String(), nullable=True),
            sa.PrimaryKeyConstraint("id", name="pk_screening_filters"),
        )


def downgrade() -> None:
    op.drop_table("screening_filters")
    op.drop_table("valuation_snapshots")
    with op.batch_alter_table("financials") as batch:
        batch.drop_column("treasury_shares")
        batch.drop_column("shares_outstanding")
        batch.drop_column("dividend_per_share")
