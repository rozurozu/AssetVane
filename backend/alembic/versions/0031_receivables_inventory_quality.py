"""売掛/在庫の質列を valuation_snapshots / us_valuation_snapshots に追加（ADR-064 #2）

Revision ID: 0031_receivables_inventory_quality
Revises: 0030_edinetdb_config
Create Date: 2026-06-30

業績の質シグナル family（#2 売掛/在庫の質）。受取債権・棚卸資産の効率（回転日数）と伸び（YoY）を
夜間に焼く。JP は edinetdb.jp の構造化財務（trade_receivables/inventories/revenue/gross_profit）、
US は yfinance balance_sheet（Receivables/Inventory）＋income_stmt（Revenue/Cost Of Revenue）。
事実（DSO/DIO・YoY）は Python（quant 純関数）が計算し、「対売上の乖離（受取債権/在庫が売上より
速く伸びていないか＝押し込み/滞留の疑い）」の解釈は revenue_growth_yoy と突き合わせて LLM が行う
（ADR-014/064）。

追加する列（いずれも nullable Float・既存運用を壊さない／docs/data-model.md と鏡写し）:
  valuation_snapshots / us_valuation_snapshots（同名・対称）:
    receivables_turnover_days（DSO）/ inventory_turnover_days（DIO）/
    receivables_growth_yoy / inventory_growth_yoy

採番: 直前 head は 0030_edinetdb_config。連鎖を直線に保つため down_revision=0030。
冪等性: schema.py が同名列込みで作成した新規 DB では既に列があるため、列が無いときだけ add する
（0021/0027/0029 と同方針）。シードは入れない（夜間バッチが埋める）。DB に触れる OS プロセスは
FastAPI 1 つ（ADR-005）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0031_receivables_inventory_quality"
down_revision: str | None = "0030_edinetdb_config"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# テーブル → 追加列。schema.py の定義と一致させる（すべて nullable Float・JP/US 対称）。
_COLUMNS: tuple[str, ...] = (
    "receivables_turnover_days",
    "inventory_turnover_days",
    "receivables_growth_yoy",
    "inventory_growth_yoy",
)
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
