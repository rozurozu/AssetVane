"""公式 EDINET（api.edinet-fsa.go.jp）接続設定（edinet_config）を追加（ADR-087）

Revision ID: 0041_edinet_config
Revises: 0040_proposal_conviction
Create Date: 2026-07-07

ADR-087。公式 EDINET の Subscription-Key（`edinet_api_key`）を env から DB（単一行）＋/settings の
WebUI へ移す＝jquants_config（ADR-061）/ edinetdb_config（ADR-064）と同型。実機で env の
`EDINET_API_KEY` に第三者サービス edinetdb.jp のキー（接頭辞 edb_）が貼られ、公式 EDINET が拒否して
夜バッチ fetch_edinet_descriptions が停止していた（documents.json が metadata.status=None）。原因は
「公式キーだけ env・edinetdb キーは DB」という置き場所の非対称。両者を /settings に並べ貼り間違いを
構造で潰す。第三者 edinetdb.jp（edinetdb_config）とは**別系統**＝命名 edinet/edinetdb で分離する。

追加するもの（docs/data-model.md と鏡写し）:
  - edinet_config … 公式 EDINET 接続（単一行・api_key 平文・GET でマスク）。plan 概念は無い
    （公式 EDINET は回数クォータ無し・レート制限のみ）ので plan 列は持たない。

採番: 直前 head は 0040_proposal_conviction。連鎖を直線に保つため down_revision=0040。
データ移行はしない（env からの自動移行もしない＝/settings で再設定する＝ADR-061/064 と同じ割り切り。
既存 env の edb_ キーはそもそも公式では無効なので移行しても意味がない）。
冪等性: テーブルが無いときだけ作る（0024/0030 と同方針）。DB に触れる OS プロセスは FastAPI 1 つ
（ADR-005）。CHECK は張らない（house style）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0041_edinet_config"
down_revision: str | None = "0040_proposal_conviction"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _existing_tables() -> set[str]:
    bind = op.get_bind()
    return set(sa.inspect(bind).get_table_names())


def upgrade() -> None:
    if "edinet_config" not in _existing_tables():
        op.create_table(
            "edinet_config",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("api_key", sa.String(), nullable=False, server_default=""),
            sa.Column("updated_at", sa.String()),
        )


def downgrade() -> None:
    if "edinet_config" in _existing_tables():
        op.drop_table("edinet_config")
