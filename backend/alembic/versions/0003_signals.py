"""signals: シグナル事前計算（Phase 1）

Revision ID: 0003_signals
Revises: 0002_fetch_meta
Create Date: 2026-06-03

Phase 1（spec §2.2・docs/phase-specs/phase1-spec.md・data-model.md §4・ADR-002・ADR-026）。
schema.py の signals 定義と一致させる。`(date, code, signal_type)` に UNIQUE を張り、
同じ夜の再実行でも冪等 UPSERT できるようにする。op.create_table で明示的に作る（0001 凍結のため）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_signals"
down_revision: str | None = "0002_fetch_meta"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "signals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("date", sa.String(), nullable=False),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("signal_type", sa.String(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("payload", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("date", "code", "signal_type", name="uq_signals_date_code_type"),
    )
    op.create_index("ix_signals_date_type", "signals", ["date", "signal_type"])
    op.create_index("ix_signals_code", "signals", ["code"])


def downgrade() -> None:
    op.drop_index("ix_signals_code", table_name="signals")
    op.drop_index("ix_signals_date_type", table_name="signals")
    op.drop_table("signals")
