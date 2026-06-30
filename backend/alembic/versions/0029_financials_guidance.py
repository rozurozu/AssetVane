"""会社予想（ガイダンス）列を financials/valuation_snapshots に追加（ADR-063 #4）

Revision ID: 0029_financials_guidance
Revises: 0028_knowledge_cards_triage_reason
Create Date: 2026-06-30

業績の質シグナル family（#4 会社ガイダンス）。J-Quants /v2/fins/summary の会社予想カラム
（FSales/FOP/FNP/FEPS＝各四半期開示に standing で載る当期FY予想・FY実績行では空）を取り込み、
夜間 calc_valuation が「予想 vs 実績の beat/miss」と「進行中FY予想の上方/下方修正」を焼く。
事実（達成率・修正率）は Python が計算し、recency/良し悪しの解釈は LLM に委ねる（ADR-014）。

追加する列（いずれも nullable Float・既存運用を壊さない／docs/data-model.md と鏡写し）:
  financials … forecast_net_sales / forecast_operating_profit / forecast_profit / forecast_eps
    （会社予想の生値。adapter._normalize_financial が FSales/FOP/FNP/FEPS から正規化）
  valuation_snapshots … op_forecast_achievement / profit_forecast_achievement /
    op_forecast_revision / profit_forecast_revision（夜間 calc_valuation が焼く達成率・修正率）

採番: 直前 head は 0028_knowledge_cards_triage_reason。連鎖を直線に保つため down_revision=0028。
冪等性: schema.py が同名列込みで作成した新規 DB では既に列があるため、列が無いときだけ add する
（0021/0027 と同方針）。シードは入れない（夜間バッチが埋める）。DB に触れる OS プロセスは
FastAPI 1 つ（ADR-005）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0029_financials_guidance"
down_revision: str | None = "0028_knowledge_cards_triage_reason"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# テーブル → 追加列（列名・型）。schema.py の定義と一致させる（ともに nullable Float）。
_ADDITIONS: dict[str, tuple[str, ...]] = {
    "financials": (
        "forecast_net_sales",
        "forecast_operating_profit",
        "forecast_profit",
        "forecast_eps",
    ),
    "valuation_snapshots": (
        "op_forecast_achievement",
        "profit_forecast_achievement",
        "op_forecast_revision",
        "profit_forecast_revision",
    ),
}


def _existing_columns(table: str) -> set[str]:
    bind = op.get_bind()
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    for table, columns in _ADDITIONS.items():
        existing = _existing_columns(table)
        for name in columns:
            if name not in existing:
                op.add_column(table, sa.Column(name, sa.Float(), nullable=True))


def downgrade() -> None:
    for table, columns in _ADDITIONS.items():
        existing = _existing_columns(table)
        for name in columns:
            if name in existing:
                op.drop_column(table, name)
