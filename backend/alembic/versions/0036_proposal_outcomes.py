"""AI 過去提案の市場結果採点台帳（proposal_outcomes）を追加（ADR-077）

Revision ID: 0036_proposal_outcomes
Revises: 0035_drop_linked_signal_type
Create Date: 2026-07-02

ADR-077（テーマ A）。夜の分析AI・チャットが出した buy/sell 提案（proposals・ADR-052）と
注目選別（notable_picks・ADR-067）を、提案日の終値を起点に N 営業日後の実現（超過）リターンで
事後採点する台帳を追加する。夜バッチ初の backward-looking ジョブ score_proposal_outcomes が
quant/outcome.py の純関数で焼き（ADR-014/016）、Tool get_track_record が集計を返す（AI は自分の
成績を pull で確認）。proposals.outcome（承認/却下の人手メモ）とは別列・別テーブルで「提示ベースの
銘柄選択スキル評価」を分離する（実 P/L ではない）。

追加するもの（docs/data-model.md と鏡写し）:
  - proposal_outcomes … origin_kind/origin_id/source/kind/code/market/entry_date/horizon/
    entry_priced_date/entry_price/as_of_date/exit_price/realized_return/benchmark_symbol/
    excess_return/benchmark_fallback/hit/status/scored_at。
    UNIQUE(origin_kind,origin_id,horizon)＋冪等 UPSERT で再実行・pending→final 上書きに耐える。

採番: 直前 head は 0035_drop_linked_signal_type。連鎖を直線に保つため down_revision=0035。
シードは入れない（夜間バッチが埋める）。冪等性: テーブルが無いときだけ作る（0032 と同方針）。
DB に触れる OS プロセスは FastAPI 1 つ（ADR-005）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0036_proposal_outcomes"
down_revision: str | None = "0035_drop_linked_signal_type"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _existing_tables() -> set[str]:
    bind = op.get_bind()
    return set(sa.inspect(bind).get_table_names())


def upgrade() -> None:
    if "proposal_outcomes" not in _existing_tables():
        op.create_table(
            "proposal_outcomes",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("origin_kind", sa.String(), nullable=False),
            sa.Column("origin_id", sa.Integer(), nullable=False),
            sa.Column("source", sa.String(), nullable=False),
            sa.Column("kind", sa.String(), nullable=False),
            sa.Column("code", sa.String(), nullable=False),
            sa.Column("market", sa.String(), nullable=False),
            sa.Column("entry_date", sa.String(), nullable=False),
            sa.Column("horizon", sa.Integer(), nullable=False),
            sa.Column("entry_priced_date", sa.String()),
            sa.Column("entry_price", sa.Float()),
            sa.Column("as_of_date", sa.String()),
            sa.Column("exit_price", sa.Float()),
            sa.Column("realized_return", sa.Float()),
            sa.Column("benchmark_symbol", sa.String()),
            sa.Column("excess_return", sa.Float()),
            sa.Column("benchmark_fallback", sa.Integer()),
            sa.Column("hit", sa.Integer()),
            sa.Column("status", sa.String(), nullable=False, server_default="pending"),
            sa.Column("scored_at", sa.String()),
            sa.UniqueConstraint(
                "origin_kind", "origin_id", "horizon", name="uq_proposal_outcomes_origin_horizon"
            ),
        )
        op.create_index("ix_proposal_outcomes_status", "proposal_outcomes", ["status"])
        op.create_index(
            "ix_proposal_outcomes_agg", "proposal_outcomes", ["source", "kind", "horizon"]
        )
        op.create_index("ix_proposal_outcomes_entry", "proposal_outcomes", ["entry_date"])


def downgrade() -> None:
    if "proposal_outcomes" in _existing_tables():
        op.drop_index("ix_proposal_outcomes_entry", table_name="proposal_outcomes")
        op.drop_index("ix_proposal_outcomes_agg", table_name="proposal_outcomes")
        op.drop_index("ix_proposal_outcomes_status", table_name="proposal_outcomes")
        op.drop_table("proposal_outcomes")
