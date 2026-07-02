"""J-Quants 接続（api_key/plan）を DB へ移す jquants_config テーブルを追加（ADR-061）

Revision ID: 0024_jquants_config
Revises: 0023_llm_reasoning_embedding
Create Date: 2026-06-24

ADR-061（ADR-058/059 と同方針）。J-Quants V2 の api_key と契約プラン名（free/light/standard/
premium）を env（起動時固定）から DB（jquants_config・単一行運用）へ移し、/settings の WebUI から
編集できるようにする。スロットル間隔（秒）は env でお守りせず adapters/jquants.py の _PLAN_INTERVALS
がプラン名から決める（ADR-008）。

追加するもの（docs/data-model.md と鏡写し）:
  - jquants_config … J-Quants V2 接続（単一行・api_key は平文・GET でマスク・plan 既定 "free"）。

採番: 直前 head は 0023_llm_reasoning_embedding。連鎖を直線に保つため down_revision=0023。
データ移行はしない（env からの自動移行もしない＝既存 JQUANTS_API_KEY/PLAN 利用者は /settings で
再設定する＝ADR-058/059 と同じ割り切り）。冪等性: テーブルが無いときだけ作る
（0011〜0023 と同方針）。DB に触れる OS プロセスは FastAPI 1 つ（ADR-005）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0024_jquants_config"
down_revision: str | None = "0023_llm_reasoning_embedding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _existing_tables() -> set[str]:
    bind = op.get_bind()
    return set(sa.inspect(bind).get_table_names())


def upgrade() -> None:
    if "jquants_config" not in _existing_tables():
        op.create_table(
            "jquants_config",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("api_key", sa.String(), nullable=False, server_default=""),
            sa.Column("plan", sa.String(), nullable=False, server_default="free"),
            sa.Column("updated_at", sa.String()),
        )


def downgrade() -> None:
    if "jquants_config" in _existing_tables():
        op.drop_table("jquants_config")
