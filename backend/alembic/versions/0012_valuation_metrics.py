"""valuation_snapshots にファンダ指標列を追加（ROE/利益率/YoY 成長率・ADR-048）

Revision ID: 0012_valuation_metrics
Revises: 0011_general_news
Create Date: 2026-06-07

ADR-048（銘柄バリュエーション判断基準）。既存 valuation_snapshots（ADR-031）は PER/PBR/時価総額/
配当利回りだけを持つ。AI Advisor が ROE/PER/PBR を根拠に割安/割高を解釈できるよう、当期FYから
ROE(=eps/bps)・営業利益率・純利益率、前期FYと突合した売上/営業利益/純利益/EPS の YoY 成長率を
1 銘柄 1 行に焼く列を足す（計算は quant.valuation の純関数・夜間 calc_valuation が UPSERT）。
market/currency 列は足さない（Tool 契約で定数返し・列は米株 Phase 7(B)＝ADR-039）。
採番: 直前 head は 0011_general_news。連鎖を直線に保つため down_revision=0011。冪等性: 既存 DB への
再適用に備え、列が無いときだけ追加する（0007/0008/0010/0011 と同方針）。
DB に触れる OS プロセスは FastAPI 1 つ（ADR-005）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012_valuation_metrics"
down_revision: str | None = "0011_general_news"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 追加するファンダ指標列（すべて Float nullable・計算不能は NULL＝捏造しない・ADR-014）
_NEW_COLUMNS: tuple[str, ...] = (
    "roe",
    "operating_margin",
    "net_margin",
    "revenue_growth_yoy",
    "op_growth_yoy",
    "profit_growth_yoy",
    "eps_growth_yoy",
)


def _existing_columns(table: str) -> set[str]:
    bind = op.get_bind()
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    existing = _existing_columns("valuation_snapshots")
    for name in _NEW_COLUMNS:
        if name not in existing:
            op.add_column("valuation_snapshots", sa.Column(name, sa.Float(), nullable=True))


def downgrade() -> None:
    existing = _existing_columns("valuation_snapshots")
    for name in reversed(_NEW_COLUMNS):
        if name in existing:
            op.drop_column("valuation_snapshots", name)
