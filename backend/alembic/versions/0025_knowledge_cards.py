"""知識カード基盤 knowledge_cards テーブルを追加（ADR-062）

Revision ID: 0025_knowledge_cards
Revises: 0024_jquants_config
Create Date: 2026-06-26

ADR-062（知識カード基盤の再設計）。AI アドバイザーの「③知識軸」を、CORE（規律・不変・リポジトリ）
／POLICY（方針・可変・DB）に続く第 3 の知識源として DB 化する。旧・手法カード（cards/*.md・全カード
常時注入＝ADR-016/048）を廃し、増える知識（市場文脈・外部メモ等）を 1 表に集約して UI 管理・
RAG 取得する。data-model.md の将来予約 method_cards を実体化＋改名（method の 3 分裂を解消）。

追加するもの（docs/data-model.md と鏡写し）:
  - knowledge_cards … 知識カード（title/body/when_to_apply/status/構造タグ/linked_signal_type/
    quant_note/always_inject/source/embedding 3 列/created_at/updated_at）。

採番: 直前 head は 0024_jquants_config。連鎖を直線に保つため down_revision=0024。
冪等性: テーブルが無いときだけ作る（0011〜0024 と同方針）。DB に触れる OS プロセスは FastAPI 1 つ
（ADR-005）。シードはマイグレーションでは入れない（既存 cards/*.md からの移行は別途・ADR-062）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0025_knowledge_cards"
down_revision: str | None = "0024_jquants_config"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _existing_tables() -> set[str]:
    bind = op.get_bind()
    return set(sa.inspect(bind).get_table_names())


def upgrade() -> None:
    if "knowledge_cards" not in _existing_tables():
        op.create_table(
            "knowledge_cards",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("title", sa.String(), nullable=False),
            sa.Column("body", sa.String(), nullable=False),
            sa.Column("when_to_apply", sa.String()),
            sa.Column("status", sa.String(), nullable=False, server_default="draft"),
            sa.Column("level", sa.String()),
            sa.Column("sector17_code", sa.String()),
            sa.Column("theme", sa.String()),
            sa.Column("linked_signal_type", sa.String()),
            sa.Column("quant_note", sa.String()),
            sa.Column("always_inject", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("source", sa.String()),
            sa.Column("embedding", sa.LargeBinary()),
            sa.Column("embed_model", sa.String()),
            sa.Column("embedded_at", sa.String()),
            sa.Column("created_at", sa.String()),
            sa.Column("updated_at", sa.String()),
        )
        op.create_index("ix_knowledge_cards_status", "knowledge_cards", ["status"])


def downgrade() -> None:
    if "knowledge_cards" in _existing_tables():
        op.drop_index("ix_knowledge_cards_status", table_name="knowledge_cards")
        op.drop_table("knowledge_cards")
