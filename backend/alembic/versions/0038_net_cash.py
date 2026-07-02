"""清原式ネットキャッシュ列を valuation_snapshots / us_valuation_snapshots に追加（ADR-079）

Revision ID: 0038_net_cash
Revises: 0037_judgment_fts
Create Date: 2026-07-02

清原達郎式のネットキャッシュ・バリュー株発掘（低PER×小型×ネットキャッシュ比率≥1）を
screen で使えるようにする。net_cash＝流動資産＋投資有価証券×0.7−総負債（BS 由来の絶対額）。
JP は edinetdb.jp の構造化財務（current_assets/total_liabilities/cash）から v1 は簡略式
（投資有価証券項を省く＝保守的に過小評価）、US は yfinance balance_sheet
（Current Assets/Investments And Advances/Total Liabilities Net Minority Interest）でフル式。
事実（net_cash）は Python（quant.net_cash 純関数）が計算し、割安の良し悪しは LLM が解釈（ADR-014）。

ネットキャッシュ比率（net_cash / 時価総額）は物理列にせず read-time で導出する（時価総額は日次で
動くが net_cash は四半期ごと・per_sector_pctile/market_cap_rank と同じ read-time 方式＝ADR-079）。
だから追加する物理列は net_cash だけ（両テーブル同名・nullable Float・既存運用を壊さない）。

採番: 直前 head は 0037_judgment_fts。冪等性: schema.py が同名列込みで作成した新規 DB では既に列が
あるため、列が無いときだけ add する（0031 と同方針）。シードは入れない（夜間バッチが埋める）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0038_net_cash"
down_revision: str | None = "0037_judgment_fts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# テーブル → 追加列。schema.py と一致（nullable Float・JP/US 対称）。比率は read-time 導出で列なし。
_COLUMNS: tuple[str, ...] = ("net_cash",)
_TABLES: tuple[str, ...] = ("valuation_snapshots", "us_valuation_snapshots")


def _existing_columns(table: str) -> set[str]:
    bind = op.get_bind()
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    for table in _TABLES:
        existing = _existing_columns(table)
        for name in _COLUMNS:
            if name not in existing:
                op.add_column(table, sa.Column(name, sa.Float(), nullable=True))


def downgrade() -> None:
    for table in _TABLES:
        existing = _existing_columns(table)
        for name in _COLUMNS:
            if name in existing:
                op.drop_column(table, name)
