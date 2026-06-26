"""knowledge_cards に weight 列を追加（ADR-062 追補・重み付き retrieval）

Revision ID: 0026_knowledge_cards_weight
Revises: 0025_knowledge_cards
Create Date: 2026-06-26

ADR-062 追補。知識カードに重要度 weight（既定 1.0）を持たせ、retrieval ランクと注入順に
`distance / weight` で効かせる（重いほど上位）。古い/信頼度が下がったカードは weight を下げて
削除せず生かす（created_at の鮮度と併せて AI が解釈）。手動編集＋チャット AI が承認制で変更する。

採番: 直前 head は 0025_knowledge_cards。冪等: 列が無いときだけ足す。既存行は default 1.0。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0026_knowledge_cards_weight"
down_revision: str | None = "0025_knowledge_cards"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns() -> set[str]:
    bind = op.get_bind()
    return {c["name"] for c in sa.inspect(bind).get_columns("knowledge_cards")}


def upgrade() -> None:
    if "knowledge_cards" in sa.inspect(op.get_bind()).get_table_names():
        if "weight" not in _columns():
            op.add_column(
                "knowledge_cards",
                sa.Column("weight", sa.Float(), nullable=False, server_default="1.0"),
            )


def downgrade() -> None:
    if "knowledge_cards" in sa.inspect(op.get_bind()).get_table_names():
        if "weight" in _columns():
            op.drop_column("knowledge_cards", "weight")
