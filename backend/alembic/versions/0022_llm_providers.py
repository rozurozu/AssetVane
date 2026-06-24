"""LLM プロバイダ複数登録・面別 provider/model 設定の 2 テーブルを追加（ADR-058）

Revision ID: 0022_llm_providers
Revises: 0021_us_stocks_yoy
Create Date: 2026-06-24

ADR-058。LLM 接続の正本を env（起動時固定 singleton）から DB へ移し、/settings の WebUI から
複数 provider を登録し、面（chat/nightly/dossier/tagger）ごとに provider と model を割り当てる。
OpenAI 互換 1 本で全 provider を吸収し、真に特殊なのは codex のみ（codex は llm_providers に行を
持たない＝鍵なし組み込み・provider_id=0 を services/llm_config がセンチネルとして扱う）。

追加するテーブル（docs/data-model.md「LLM プロバイダ・面別設定」節と鏡写し）:
  - llm_providers   … 鍵あり provider のレジストリ（複数行・name 一意・api_key は平文＝ADR-001。
                       将来は暗号化＝ADR-058 に明記）
  - llm_face_config … 面（face）→ {provider_id, model} の割当（4 行運用・provider_id は
                       NULL=未設定 / 0=codex / >0=llm_providers.id・FK は張らない）

採番: 直前 head は 0021_us_stocks_yoy。連鎖を直線に保つため down_revision=0021。
データ移行はしない（シードなし＝ADR-058 確定4。既存 env 利用者は /settings で手動再登録）。
冪等性: 既存 DB への再適用に備え、テーブルが無いときだけ create する（0011〜0021 と同方針）。
DB に触れる OS プロセスは FastAPI 1 つ（ADR-005）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0022_llm_providers"
down_revision: str | None = "0021_us_stocks_yoy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _existing_tables() -> set[str]:
    bind = op.get_bind()
    return set(sa.inspect(bind).get_table_names())


def upgrade() -> None:
    tables = _existing_tables()

    if "llm_providers" not in tables:
        op.create_table(
            "llm_providers",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("base_url", sa.String(), nullable=False),
            sa.Column("api_key", sa.String(), nullable=False, server_default=""),
            sa.Column("default_model", sa.String(), nullable=False, server_default=""),
            sa.Column("created_at", sa.String()),
            sa.Column("updated_at", sa.String()),
            sa.UniqueConstraint("name", name="uq_llm_providers_name"),
        )

    if "llm_face_config" not in tables:
        op.create_table(
            "llm_face_config",
            sa.Column("face", sa.String(), primary_key=True),
            sa.Column("provider_id", sa.Integer(), nullable=True),
            sa.Column("model", sa.String(), nullable=False, server_default=""),
            sa.Column("updated_at", sa.String()),
        )


def downgrade() -> None:
    tables = _existing_tables()
    if "llm_face_config" in tables:
        op.drop_table("llm_face_config")
    if "llm_providers" in tables:
        op.drop_table("llm_providers")
