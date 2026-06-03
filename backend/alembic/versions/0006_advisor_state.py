"""advisor_state: AI Advisor の状態テーブル群（Phase 3）

Revision ID: 0006_advisor_state
Revises: 0005_financials
Create Date: 2026-06-03

Phase 3（phase3-spec.md §2.1・ADR-011〜016/028）。
policy / advisor_journal / proposals / llm_usage を作成。
- policy は id 固定の 1 行運用（ADR-013・版管理なし）。比率系は 0..1（決定2）。
- advisor_journal は source で nightly/chat を区別（ADR-029）。
- proposals は depends_on で承認順制御（policy_change→buy・決定4/B-8）。自分データ系の
  FK（journal_id→advisor_journal.id・depends_on→proposals.id）を張る。
- llm_usage は OpenRouter 実コスト台帳（ADR-028・spec §7.1）。
DB に触れる OS プロセスは FastAPI 1 つ（ADR-005）。busy_timeout は engine.py で既設。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_advisor_state"
down_revision: str | None = "0005_financials"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # policy: 投資方針（1 行運用・ADR-013）。比率系はすべて 0..1（決定2）。
    op.create_table(
        "policy",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),  # id 固定
        sa.Column("risk_tolerance", sa.String(), nullable=True),  # "低"/"中"/"高"
        sa.Column("time_horizon", sa.String(), nullable=True),  # "短"/"中"/"長"
        sa.Column("target_cash_ratio", sa.Float(), nullable=True),  # 0..1
        sa.Column("max_position_weight", sa.Float(), nullable=True),  # 0..1
        sa.Column("sector_caps", sa.String(), nullable=True),  # JSON {sector33_code: 0..1}
        sa.Column("target_return", sa.Float(), nullable=True),  # 0..1（任意）
        sa.Column("no_leverage", sa.Integer(), nullable=True),  # 0/1
        sa.Column("exclusions", sa.String(), nullable=True),  # JSON ["7203", ...]
        sa.Column("rationale", sa.String(), nullable=True),  # 自由文の理念（U-7・即時更新可）
        sa.Column("updated_at", sa.String(), nullable=True),  # ISO8601
        sa.PrimaryKeyConstraint("id"),
    )

    # advisor_journal: 投資日記（夜=1件/日・チャット昇格も同一テーブル＝ADR-029）。
    op.create_table(
        "advisor_journal",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("date", sa.String(), nullable=False),  # 'YYYY-MM-DD'
        sa.Column("source", sa.String(), server_default="nightly", nullable=False),
        sa.Column("situation_briefing", sa.String(), nullable=True),  # JSON（監査用）
        sa.Column("observations", sa.String(), nullable=True),  # AI 所見
        sa.Column("proposal", sa.String(), nullable=True),  # 当日の提案
        sa.Column("proposed_policy_change", sa.String(), nullable=True),  # JSON {field,from,to}
        sa.Column("policy_snapshot", sa.String(), nullable=True),  # JSON（policy まるごと）
        sa.Column("llm_model", sa.String(), nullable=True),  # 監査用
        sa.Column("created_at", sa.String(), nullable=True),  # ISO8601
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_advisor_journal_date", "advisor_journal", ["date"])

    # proposals: 提案（承認状態のみ・約定しない＝ADR-001/019）。承認順制御 depends_on（決定4/B-8）。
    op.create_table(
        "proposals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_date", sa.String(), nullable=False),  # 'YYYY-MM-DD'
        sa.Column("kind", sa.String(), nullable=False),  # policy_change/buy/sell/rebalance
        sa.Column("body", sa.String(), nullable=True),  # JSON（kind 依存）
        sa.Column("rationale", sa.String(), nullable=True),  # 根拠
        sa.Column("status", sa.String(), server_default="pending", nullable=False),
        sa.Column("outcome", sa.String(), nullable=True),
        sa.Column("resolved_at", sa.String(), nullable=True),
        sa.Column("journal_id", sa.Integer(), nullable=True),  # FK→advisor_journal.id
        sa.Column("depends_on", sa.Integer(), nullable=True),  # FK→proposals.id（承認順制御）
        sa.ForeignKeyConstraint(["journal_id"], ["advisor_journal.id"]),
        sa.ForeignKeyConstraint(["depends_on"], ["proposals.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_proposals_status", "proposals", ["status"])

    # llm_usage: LLM コスト台帳（ADR-028・spec §7.1）。OpenRouter usage.cost を per-call で積む。
    op.create_table(
        "llm_usage",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),  # ISO8601
        sa.Column("source", sa.String(), nullable=False),  # "nightly"/"chat"/"dossier"
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Float(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_llm_usage_created_at", "llm_usage", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_llm_usage_created_at", table_name="llm_usage")
    op.drop_table("llm_usage")
    op.drop_index("ix_proposals_status", table_name="proposals")
    op.drop_table("proposals")
    op.drop_index("ix_advisor_journal_date", table_name="advisor_journal")
    op.drop_table("advisor_journal")
    op.drop_table("policy")
