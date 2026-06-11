"""FX レートと米株保有管理テーブル一式、asset_snapshots.us_stock_value を追加（ADR-057）

Revision ID: 0019_us_holdings_fx
Revises: 0018_themes
Create Date: 2026-06-11

ADR-057（Phase 7(B-2) FX/保有波及）。Phase 7(B-1)（ADR-055）で米株を提示専用として持ったが、
JPY 単一前提の資産評価コアには触れず currency 列も保有テーブルも持たなかった。本フェーズで
(a) FX 基盤 (b) 米株保有管理 (c) 資産概要合算 を最小スコープで足し、米株保有を JPY 資産概要に
合算できるようにする。市場分離（ADR-031）は維持＝米株は別テーブルで持ち、合算は資産概要レイヤの
FX 換算でのみ行う。

追加するもの:
  - fx_rates                    … FX 日足終値（(date,pair) PK・rate は JPY/USD＝yfinance JPY=X）
  - us_transactions             … 米株取引（一次データ・symbol→us_stocks FK・price は USD・約定時
                                   fx_rate を記録）。日本株 transactions をミラー＋fx_rate/note。
  - us_holdings                 … 米株保有（取引から導出・UNIQUE(symbol)・avg_cost(USD) と
                                   avg_cost_jpy(取得時レートで JPY 固定した移動平均原価)）
  - asset_snapshots.us_stock_value … 日次総資産に米株評価額バケット（JPY 換算後）を追加

採番: 直前 head は 0018_themes。連鎖を直線に保つため down_revision=0018。
冪等性: 既存 DB への再適用に備え、テーブル/列が無いときだけ作る（0011〜0018 と同方針）。
DB に触れる OS プロセスは FastAPI 1 つ（ADR-005）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0019_us_holdings_fx"
down_revision: str | None = "0018_themes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _existing_tables() -> set[str]:
    bind = op.get_bind()
    return set(sa.inspect(bind).get_table_names())


def _existing_columns(table: str) -> set[str]:
    bind = op.get_bind()
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    tables = _existing_tables()

    # (a) fx_rates: FX 日足終値。(date,pair) 複合 PK で UPSERT 冪等。rate は JPY/USD（JPY=X 終値）。
    #     FK は張らない（生データ流儀＝index_quotes/fund_navs と同方針）。
    if "fx_rates" not in tables:
        op.create_table(
            "fx_rates",
            sa.Column("date", sa.String(), nullable=False),  # 営業日 'YYYY-MM-DD'
            sa.Column("pair", sa.String(), nullable=False),  # 通貨ペア 'USDJPY'
            sa.Column("rate", sa.Float(), nullable=True),  # 1 USD あたりの JPY（JPY=X 終値）
            sa.PrimaryKeyConstraint("date", "pair", name="pk_fx_rates"),
            sa.Index("ix_fx_rates_pair", "pair"),
        )

    # (b) us_transactions: 米株取引（一次データ）。symbol→us_stocks.symbol FK（誤入力防止・L-7）。
    #     price は約定単価（USD）。fx_rate は約定時 USDJPY（取得時レート固定の含み損益・ADR-057）。
    if "us_transactions" not in tables:
        op.create_table(
            "us_transactions",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("symbol", sa.String(), nullable=False),
            sa.Column("side", sa.String(), nullable=False),  # 'buy' / 'sell'
            sa.Column("shares", sa.Float(), nullable=False),
            sa.Column("price", sa.Float(), nullable=False),  # 約定単価（USD）
            sa.Column("fee", sa.Float(), nullable=True),  # 手数料（USD・avg_cost に含めない）
            sa.Column("traded_at", sa.String(), nullable=False),  # 約定日 'YYYY-MM-DD'
            sa.Column("fx_rate", sa.Float(), nullable=False),  # 約定時 USDJPY（JPY/USD）
            sa.Column("note", sa.String(), nullable=True),  # 任意メモ
            sa.PrimaryKeyConstraint("id", name="pk_us_transactions"),
            sa.ForeignKeyConstraint(
                ["symbol"], ["us_stocks.symbol"], name="fk_us_transactions_symbol_us_stocks"
            ),
        )
        op.create_index("ix_us_transactions_symbol", "us_transactions", ["symbol"])

    # (c) us_holdings: 米株保有（取引から導出）。単一ユーザー（ADR-001）ゆえ portfolio で割らず
    #     UNIQUE(symbol) を UPSERT キーとする。avg_cost は USD 建て移動平均、avg_cost_jpy は
    #     取得時レートで JPY 固定した移動平均原価（為替損益を含み損益に乗せる素・ADR-057）。
    if "us_holdings" not in tables:
        op.create_table(
            "us_holdings",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("symbol", sa.String(), nullable=False),
            sa.Column("shares", sa.Float(), nullable=False),  # 導出: Σbuy − Σsell
            sa.Column("avg_cost", sa.Float(), nullable=True),  # 導出: 移動平均取得単価（USD）
            sa.Column("avg_cost_jpy", sa.Float(), nullable=True),  # 導出: 取得時レート JPY 固定原価
            sa.PrimaryKeyConstraint("id", name="pk_us_holdings"),
            sa.ForeignKeyConstraint(
                ["symbol"], ["us_stocks.symbol"], name="fk_us_holdings_symbol_us_stocks"
            ),
            sa.UniqueConstraint("symbol", name="uq_us_holdings_symbol"),
        )

    # (d) asset_snapshots に us_stock_value 列を追加（米株評価額バケット・JPY 換算後）
    if "us_stock_value" not in _existing_columns("asset_snapshots"):
        op.add_column("asset_snapshots", sa.Column("us_stock_value", sa.Float(), nullable=True))


def downgrade() -> None:
    tables = _existing_tables()

    if "us_stock_value" in _existing_columns("asset_snapshots"):
        op.drop_column("asset_snapshots", "us_stock_value")

    if "us_holdings" in tables:
        op.drop_table("us_holdings")
    if "us_transactions" in tables:
        op.drop_index("ix_us_transactions_symbol", table_name="us_transactions")
        op.drop_table("us_transactions")
    if "fx_rates" in tables:
        op.drop_index("ix_fx_rates_pair", table_name="fx_rates")
        op.drop_table("fx_rates")
