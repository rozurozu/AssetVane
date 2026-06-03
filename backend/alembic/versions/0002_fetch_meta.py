"""fetch_meta: 差分取得の進捗管理（Phase 1）

Revision ID: 0002_fetch_meta
Revises: 0001_baseline
Create Date: 2026-06-03

Phase 1（spec §2.1・docs/phase-specs/phase1-spec.md・data-model.md §6）。
0001 を2表に凍結したので（fresh DB での "table already exists" 回避）、追加表は
autogenerate に頼らず op.create_table で**明示的に**作る（schema.py の定義と一致させる）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_fetch_meta"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "fetch_meta",
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("last_fetched_date", sa.String(), nullable=True),
        sa.Column("updated_at", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("source"),
    )


def downgrade() -> None:
    op.drop_table("fetch_meta")
