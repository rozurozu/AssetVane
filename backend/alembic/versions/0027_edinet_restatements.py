"""訂正有報フラグ edinet_restatements テーブルを追加（B-2・docTypeCode=130）

Revision ID: 0027_edinet_restatements
Revises: 0026_knowledge_cards_weight
Create Date: 2026-06-30

業績の質シグナル family（B-2）。EDINET 提出日クロール（fetch_edinet_descriptions）が
docTypeCode='120'（有報）だけ取り込み「捨てていた」訂正有価証券報告書（docTypeCode='130'）の
出現を、本文を取らず一覧の事実だけ記録する append-only 台帳を足す。get_valuation が
last_restatement_at（最新訂正の提出日）として中継し、recency の解釈は LLM に委ねる（ADR-014）。

追加するもの（docs/data-model.md と鏡写し）:
  - edinet_restatements … 訂正有報の出現台帳（doc_id 冪等・code/disclosed_date/filer_name/
    doc_type_code/created_at）。

採番: 直前 head は 0026_knowledge_cards_weight。連鎖を直線に保つため down_revision=0026。
冪等性: テーブルが無いときだけ作る（0011〜0026 と同方針）。シードは入れない（クロールが埋める）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0027_edinet_restatements"
down_revision: str | None = "0026_knowledge_cards_weight"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _existing_tables() -> set[str]:
    bind = op.get_bind()
    return set(sa.inspect(bind).get_table_names())


def upgrade() -> None:
    if "edinet_restatements" not in _existing_tables():
        op.create_table(
            "edinet_restatements",
            sa.Column("doc_id", sa.String(), primary_key=True),
            sa.Column("code", sa.String(), nullable=False),
            sa.Column("disclosed_date", sa.String(), nullable=False),
            sa.Column("filer_name", sa.String()),
            sa.Column("doc_type_code", sa.String()),
            sa.Column("created_at", sa.String()),
        )
        op.create_index("ix_edinet_restatements_code", "edinet_restatements", ["code"])


def downgrade() -> None:
    if "edinet_restatements" in _existing_tables():
        op.drop_index("ix_edinet_restatements_code", table_name="edinet_restatements")
        op.drop_table("edinet_restatements")
