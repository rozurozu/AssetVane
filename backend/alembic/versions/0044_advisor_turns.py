"""AI Advisor 判断軌跡の観測台帳（advisor_turns）を追加（ADR-092）

Revision ID: 0044_advisor_turns
Revises: 0043_proposal_declared_horizon
Create Date: 2026-07-08

ADR-092（AI Advisor の判断軌跡観測台帳＋夜AI単体起動口）。track_record が「結果の質」を採点する
一方で「AI が実際にどう判断したか（プロセスの質）」を残す層が無かった穴を閉じる。Tool ループ
（run_tool_loop）を通る 5 面（chat/nightly/reviewer/profiler/skeptic）の 1 ターンを 1 行に焼く。
reply 本文は残さず tool_sequence（呼んだ Tool 名＋引数）だけ焼く（結果値なし＝ADR-025・生チャット
非索引＝ADR-029）。集計に効く規律 2 列（called_propose_trade / propose_trade_disciplined）だけ
非正規化し、AVG で充足率を純 SQL 集計する（proposal_outcomes.conviction/hit と同じ流儀・ADR-084）。

追加するもの（docs/data-model.md と鏡写し・schema.py の列定義と手書きで一致させる）:
  - advisor_turns … id/created_at/source/model/tool_sequence/n_rounds/truncated/
    called_propose_trade/propose_trade_disciplined。Index(source, created_at)。

採番: 直前 head は 0043_proposal_declared_horizon。連鎖を直線に保つため down_revision=0043。
シードは入れない（LLM ターンが埋める）。冪等性: テーブルが無いときだけ作る（0036 と同方針）。
CHECK は張らない（source 等はアプリ層で扱う・house style）。DB に触れる OS プロセスは FastAPI 1 つ
（ADR-005）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0044_advisor_turns"
down_revision: str | None = "0043_proposal_declared_horizon"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _existing_tables() -> set[str]:
    bind = op.get_bind()
    return set(sa.inspect(bind).get_table_names())


def upgrade() -> None:
    if "advisor_turns" not in _existing_tables():
        op.create_table(
            "advisor_turns",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("created_at", sa.String(), nullable=False),
            sa.Column("source", sa.String(), nullable=False),
            sa.Column("model", sa.String()),
            sa.Column("tool_sequence", sa.String()),
            sa.Column("n_rounds", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("truncated", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("called_propose_trade", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("propose_trade_disciplined", sa.Integer()),
        )
        op.create_index(
            "ix_advisor_turns_source_created", "advisor_turns", ["source", "created_at"]
        )


def downgrade() -> None:
    if "advisor_turns" in _existing_tables():
        op.drop_index("ix_advisor_turns_source_created", table_name="advisor_turns")
        op.drop_table("advisor_turns")
