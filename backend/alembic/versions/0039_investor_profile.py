"""投資家プロファイル investor_profile テーブルを追加（ADR-082）

Revision ID: 0039_investor_profile
Revises: 0038_net_cash
Create Date: 2026-07-03

ADR-082（テーマ C・★4 自己改善ループ）。AI が台帳から蒸留する「投資家の行動の癖＝記述的
プロファイル」を持つ層。policy（規範＝どうすべきか）と厳格分離し、第二の policy にしない
（版管理化しない・policy 変更は承認制 policy_change のみ）。single row（id 固定・1 枚の散文）。
夜間バッチ profiler 面が傾向メモを proposals(kind='profile_note') で承認制起票し、人間が承認
すると body に追記される（ADR-009）。注入は CORE→POLICY に続く第 3 層（鏡・反追従）。

採番: 直前 head は 0038_net_cash。連鎖を直線に保つため down_revision=0038。
冪等性: テーブルが無いときだけ作る（0011〜0025 と同方針）。シードは入れない（初回 GET は空文字）。
proposals.kind='profile_note' は既存 proposals 表（kind は自由 String・CHECK なし）を流用するため
このテーブル追加以外の migration は不要。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0039_investor_profile"
down_revision: str | None = "0038_net_cash"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _existing_tables() -> set[str]:
    bind = op.get_bind()
    return set(sa.inspect(bind).get_table_names())


def upgrade() -> None:
    if "investor_profile" not in _existing_tables():
        op.create_table(
            "investor_profile",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("body", sa.String(), nullable=False, server_default=""),
            sa.Column("updated_at", sa.String()),
        )


def downgrade() -> None:
    if "investor_profile" in _existing_tables():
        op.drop_table("investor_profile")
