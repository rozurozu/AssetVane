"""面別 reasoning_effort 列と embedding_config テーブルを追加（ADR-059）

Revision ID: 0023_llm_reasoning_embedding
Revises: 0022_llm_providers
Create Date: 2026-06-24

ADR-059（ADR-058 拡張）。面（chat/nightly/dossier/tagger）ごとに推論努力を設定できるよう
llm_face_config に reasoning_effort 列を足し、意味検索の embedding 接続
（base_url/api_key/model/dim）を env から DB（embedding_config・単一行運用）へ移す。

追加するもの（docs/data-model.md と鏡写し）:
  - llm_face_config.reasoning_effort … 空=既定（openai は送らない / codex は env フォールバック）。
  - embedding_config … 意味検索の埋め込み接続（単一行・api_key は平文・GET でマスク）。

採番: 直前 head は 0022_llm_providers。連鎖を直線に保つため down_revision=0022。
データ移行はしない（env からの自動移行もしない＝既存 EMBEDDING_* 利用者は /settings で再設定）。
冪等性: 列/テーブルが無いときだけ追加する（0011〜0022 と同方針）。DB に触れる OS プロセスは
FastAPI 1 つ（ADR-005）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0023_llm_reasoning_embedding"
down_revision: str | None = "0022_llm_providers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _existing_tables() -> set[str]:
    bind = op.get_bind()
    return set(sa.inspect(bind).get_table_names())


def _existing_columns(table: str) -> set[str]:
    bind = op.get_bind()
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    tables = _existing_tables()

    if "llm_face_config" in tables and "reasoning_effort" not in _existing_columns(
        "llm_face_config"
    ):
        op.add_column("llm_face_config", sa.Column("reasoning_effort", sa.String(), nullable=True))

    if "embedding_config" not in tables:
        op.create_table(
            "embedding_config",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("base_url", sa.String(), nullable=False, server_default=""),
            sa.Column("api_key", sa.String(), nullable=False, server_default=""),
            sa.Column("model", sa.String(), nullable=False, server_default=""),
            sa.Column("dim", sa.Integer()),
            sa.Column("updated_at", sa.String()),
        )


def downgrade() -> None:
    tables = _existing_tables()
    if "embedding_config" in tables:
        op.drop_table("embedding_config")
    if "llm_face_config" in tables and "reasoning_effort" in _existing_columns("llm_face_config"):
        op.drop_column("llm_face_config", "reasoning_effort")
