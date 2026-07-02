"""夜 digest 注目シグナルの AI 選別台帳（notable_picks）を追加（ADR-067）

Revision ID: 0032_notable_picks
Revises: 0031_receivables_inventory_quality
Create Date: 2026-07-01

ADR-067。夜 digest の「注目シグナル」を score 閾値 Top N 抽出から
「合流(confluence)ゲート＋AI 選別」へ作り直す。Python が独立材料 2 次元以上の重なりで
候補集合を組み、夜の分析AI が submit_notable_stocks で総合的に注目すべき銘柄だけを
選ぶ（ADR-014）。その選別を永続し、後続 notify_digest が読んで digest 本文に載せる
（journal/proposals と同じ「夜AI が書き digest が読む」パターン）。

追加するもの（docs/data-model.md と鏡写し）:
  - notable_picks … date/code/reason/source/created_at。source で nightly/chat を区別し digest は
    nightly を読む。UNIQUE(date,code,source) ＋ 冪等 UPSERT で再実行（POST /batch/run）
    でも重複しない。

採番: 直前 head は 0031_receivables_inventory_quality。連鎖を直線に保つため down_revision=0031。
シードは入れない（夜間バッチが埋める）。冪等性: テーブルが無いときだけ作る（0030 と同方針）。
DB に触れる OS プロセスは FastAPI 1 つ（ADR-005）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0032_notable_picks"
down_revision: str | None = "0031_receivables_inventory_quality"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _existing_tables() -> set[str]:
    bind = op.get_bind()
    return set(sa.inspect(bind).get_table_names())


def upgrade() -> None:
    if "notable_picks" not in _existing_tables():
        op.create_table(
            "notable_picks",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("date", sa.String(), nullable=False),
            sa.Column("code", sa.String(), nullable=False),
            sa.Column("reason", sa.String()),
            sa.Column("source", sa.String(), nullable=False, server_default="nightly"),
            sa.Column("created_at", sa.String()),
            sa.UniqueConstraint("date", "code", "source", name="uq_notable_picks_date_code_source"),
        )
        op.create_index("ix_notable_picks_date", "notable_picks", ["date"])


def downgrade() -> None:
    if "notable_picks" in _existing_tables():
        op.drop_index("ix_notable_picks_date", table_name="notable_picks")
        op.drop_table("notable_picks")
