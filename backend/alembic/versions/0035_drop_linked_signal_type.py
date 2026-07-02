"""非推奨列 knowledge_cards.linked_signal_type を DROP する（ADR-075 完遂）

Revision ID: 0035_drop_linked_signal_type
Revises: 0034_drop_codex_face_sentinel
Create Date: 2026-07-02

ADR-075。手法↔signal の対応は method_cards（app/advisor/method_cards/*.md）が signal_type キーで
持つため、knowledge_cards.linked_signal_type は冗長になった（0025 で新設・注入/意味検索/nightly/
Tool のどこも WHERE linked_signal_type=… を使わず飾りのままだった＝ADR-016 の「索引」役は
method_cards が引き継ぐ）。ADR-075 は非推奨化（triage 生成停止＋schema コメント）まで済ませ「列 DROP
は別 PR」と先送りしていた本手順の完遂。デッドコードを残さないため列を物理削除する。

採番: 直前 head は 0034_drop_codex_face_sentinel。連鎖を直線に保つため down_revision=0034。
SQLite は DROP COLUMN でテーブル再構築を伴うため batch_alter_table で囲む（ADD は素で受けるので
downgrade の再追加は直接 op.add_column・0009/0033 の前例と同じ）。downgrade は空の nullable 列を
再追加するのみ＝既存値は復元不能（deprecated 列で method_cards が索引を持つため破棄で可・0034 と
同哲学）。DB に触れる OS プロセスは FastAPI 1 つ（ADR-005）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0035_drop_linked_signal_type"
down_revision: str | None = "0034_drop_codex_face_sentinel"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLite は DROP COLUMN でテーブル再構築が要るため batch_alter_table で囲む（ADR-075）。
    with op.batch_alter_table("knowledge_cards") as batch:
        batch.drop_column("linked_signal_type")


def downgrade() -> None:
    # 空の nullable 列を再追加する（既存値は復元不能・deprecated 列ゆえ破棄で可）。
    op.add_column("knowledge_cards", sa.Column("linked_signal_type", sa.String(), nullable=True))
