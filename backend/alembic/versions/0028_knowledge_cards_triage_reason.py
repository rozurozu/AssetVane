"""knowledge_cards に AI 審査理由 triage_reason 列を追加（ADR-062 追補・雑追加リデザイン）

Revision ID: 0028_knowledge_cards_triage_reason
Revises: 0027_edinet_restatements
Create Date: 2026-06-30

知識カードの「雑追加」リデザイン（本文だけ貼って『追加』→ 追加時に AI が同期で
title/when_to_apply/level 生成＋verdict 審査）。verdict の理由（reason）を再読込後も残せるよう
triage_reason 列を足す。status は verdict を反映済みだが reason は文字列なので別列で持つ
（一覧に verdict＋reason を常表示する）。

追加するもの（docs/data-model.md と鏡写し）:
  - knowledge_cards.triage_reason … AI 審査（assist_card）の判定理由（nullable・AI 未整形は NULL）。

採番: 直前 head は 0027_edinet_restatements。連鎖を直線に保つため down_revision=0027。
冪等性: 列が無いときだけ add する（0011〜0027 と同方針・SQLite は ADD COLUMN 可）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0028_knowledge_cards_triage_reason"
down_revision: str | None = "0027_edinet_restatements"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _existing_columns(table: str) -> set[str]:
    bind = op.get_bind()
    return {col["name"] for col in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    if "triage_reason" not in _existing_columns("knowledge_cards"):
        op.add_column("knowledge_cards", sa.Column("triage_reason", sa.String(), nullable=True))


def downgrade() -> None:
    if "triage_reason" in _existing_columns("knowledge_cards"):
        op.drop_column("knowledge_cards", "triage_reason")
