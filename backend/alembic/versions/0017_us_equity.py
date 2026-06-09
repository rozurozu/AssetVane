"""米株 3 テーブルを追加（us_stocks / us_daily_quotes / us_valuation_snapshots・Phase 7(B-1)）

Revision ID: 0017_us_equity
Revises: 0016_news_embedding
Create Date: 2026-06-09

ADR-031/039/048/055（Phase 7(B-1) 米国株スクリーナー・提示専用）。米株は日本株コアと物理的に
別テーブルで持つ（ADR-031 市場分離）。JPY 単一前提の資産評価コアには一切触れず、提示専用に閉じる
（currency 列も持たない＝FX/保有は Phase 7(B-2) 送り）。データ源は yfinance＝UsEquityAdapter
（ADR-039(B)）。業種は Yahoo `.info.sector`（GICS 相当）を文字列保持（ADR-055）。

追加するテーブル:
  - us_stocks               … 米株マスタ（symbol PK・業種/財務素・is_etf フラグ）
  - us_daily_quotes         … 米株日足四本値（(symbol,date) PK・チャート用全履歴・FK なし）
  - us_valuation_snapshots  … 米株バリュエーション（symbol PK・FK→us_stocks・1 銘柄最新 1 行）

採番: 直前 head は 0016_news_embedding。連鎖を直線に保つため down_revision=0016。
冪等性: 既存 DB への再適用に備え、テーブルが無いときだけ create する（0011〜0016 と同方針）。
DB に触れる OS プロセスは FastAPI 1 つ（ADR-005）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017_us_equity"
down_revision: str | None = "0016_news_embedding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _existing_tables() -> set[str]:
    bind = op.get_bind()
    return set(sa.inspect(bind).get_table_names())


def upgrade() -> None:
    tables = _existing_tables()

    if "us_stocks" not in tables:
        op.create_table(
            "us_stocks",
            sa.Column("symbol", sa.String(), primary_key=True),
            sa.Column("company_name", sa.String()),
            sa.Column("gics_sector", sa.String()),
            sa.Column("industry", sa.String()),
            sa.Column("is_etf", sa.Integer()),
            sa.Column("eps", sa.Float()),
            sa.Column("bps", sa.Float()),
            sa.Column("shares_net", sa.Float()),
            sa.Column("dividend_per_share", sa.Float()),
            sa.Column("net_sales", sa.Float()),
            sa.Column("operating_profit", sa.Float()),
            sa.Column("profit", sa.Float()),
            # YoY 中継列（ADR-055）。`.info` 提供の YoY 率（実値）を us_valuation_snapshots へ転記。
            sa.Column("revenue_growth_yoy", sa.Float()),  # 売上 YoY（`.info.revenueGrowth`）
            sa.Column("earnings_growth_yoy", sa.Float()),  # 純利益 YoY（`.info.earningsGrowth`）
            sa.Column("fin_disclosed_date", sa.String()),
            sa.Column("updated_at", sa.String()),
        )

    if "us_daily_quotes" not in tables:
        op.create_table(
            "us_daily_quotes",
            sa.Column("symbol", sa.String(), nullable=False),
            sa.Column("date", sa.String(), nullable=False),
            sa.Column("open", sa.Float()),
            sa.Column("high", sa.Float()),
            sa.Column("low", sa.Float()),
            sa.Column("close", sa.Float()),
            sa.Column("volume", sa.Float()),
            sa.Column("adj_close", sa.Float()),
            sa.PrimaryKeyConstraint("symbol", "date", name="pk_us_daily_quotes"),
        )
        op.create_index("ix_us_daily_quotes_symbol", "us_daily_quotes", ["symbol"])
        op.create_index("ix_us_daily_quotes_date", "us_daily_quotes", ["date"])

    if "us_valuation_snapshots" not in tables:
        op.create_table(
            "us_valuation_snapshots",
            sa.Column(
                "symbol",
                sa.String(),
                sa.ForeignKey("us_stocks.symbol"),
                primary_key=True,
            ),
            sa.Column("as_of_date", sa.String(), nullable=False),
            sa.Column("close", sa.Float()),
            sa.Column("eps", sa.Float()),
            sa.Column("bps", sa.Float()),
            sa.Column("dividend_per_share", sa.Float()),
            sa.Column("shares_net", sa.Float()),
            sa.Column("per", sa.Float()),
            sa.Column("pbr", sa.Float()),
            sa.Column("market_cap", sa.Float()),
            sa.Column("dividend_yield", sa.Float()),
            sa.Column("roe", sa.Float()),
            sa.Column("operating_margin", sa.Float()),
            sa.Column("net_margin", sa.Float()),
            sa.Column("revenue_growth_yoy", sa.Float()),
            sa.Column("op_growth_yoy", sa.Float()),
            sa.Column("profit_growth_yoy", sa.Float()),
            sa.Column("eps_growth_yoy", sa.Float()),
            sa.Column("fin_disclosed_date", sa.String()),
            sa.Column("updated_at", sa.String()),
        )


def downgrade() -> None:
    tables = _existing_tables()
    if "us_valuation_snapshots" in tables:
        op.drop_table("us_valuation_snapshots")
    if "us_daily_quotes" in tables:
        op.drop_index("ix_us_daily_quotes_date", table_name="us_daily_quotes")
        op.drop_index("ix_us_daily_quotes_symbol", table_name="us_daily_quotes")
        op.drop_table("us_daily_quotes")
    if "us_stocks" in tables:
        op.drop_table("us_stocks")
