"""baseline: stocks / daily_quotes（Phase 0）

Revision ID: 0001_baseline
Revises:
Create Date: 2026-06-02

Phase 0 時点のスキーマ。`metadata.create_all/drop_all` で作るので schema.py と必ず一致し、
既存 DB（create_all で先に作られた状態）に対して upgrade しても CREATE は IF NOT EXISTS
相当で**非破壊**。以降のスキーマ変更（financials/signals 等）は autogenerate で別リビジョンに刻む。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from app.db.schema import metadata

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    metadata.drop_all(bind=op.get_bind())
