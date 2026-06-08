"""投信の保有管理テーブル一式と asset_snapshots.fund_value を追加（ADR-054）

Revision ID: 0015_funds
Revises: 0014_fetch_meta_attempt_ok
Create Date: 2026-06-08

ADR-054（投信の保有を株と同じ「取引→導出」構造で本格管理する）。当初方針（投信は割合文脈で
深追いしない）を本 ADR で上書きし、非上場投信（オルカン等）の基準価額 NAV を投信総合検索
ライブラリー CSV から日次取得して含み損益を随時計算する。識別子は ISIN（NAV 取得が ISIN 必須）。

追加するもの:
  - funds            … 投信マスタ（isin PK・name・assoc_code 協会コード任意）
  - fund_navs        … NAV 時系列（(isin,date) PK・nav は 10,000 口あたりの円）
  - fund_transactions… 投信取引（一次データ・isin→funds.isin FK・units/price）
  - fund_holdings    … 投信保有（取引から導出・(portfolio_id,isin) UNIQUE）
  - asset_snapshots.fund_value … 日次総資産に投信評価額バケットを追加

採番: 直前 head は 0014_fetch_meta_attempt_ok。連鎖を直線に保つため down_revision=0014。
冪等性: 既存 DB への再適用に備え、テーブル/列が無いときだけ作る（0011〜0014 と同方針）。
DB に触れる OS プロセスは FastAPI 1 つ（ADR-005）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0015_funds"
down_revision: str | None = "0014_fetch_meta_attempt_ok"
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

    # (a) funds: 投信マスタ。ISIN を PK にし、ユーザーが一度だけ登録する。
    if "funds" not in tables:
        op.create_table(
            "funds",
            sa.Column("isin", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("assoc_code", sa.String(), nullable=True),  # 協会コード（表示用）
            sa.Column("updated_at", sa.String(), nullable=True),
            sa.PrimaryKeyConstraint("isin", name="pk_funds"),
        )

    # (b) fund_navs: NAV 時系列。(isin,date) PK で UPSERT 冪等。FK は張らない（生データ流儀）。
    if "fund_navs" not in tables:
        op.create_table(
            "fund_navs",
            sa.Column("isin", sa.String(), nullable=False),
            sa.Column("date", sa.String(), nullable=False),  # 基準日 'YYYY-MM-DD'
            sa.Column("nav", sa.Float(), nullable=True),  # 10,000 口あたりの円
            sa.PrimaryKeyConstraint("isin", "date", name="pk_fund_navs"),
        )
        op.create_index("ix_fund_navs_isin", "fund_navs", ["isin"])
        op.create_index("ix_fund_navs_date", "fund_navs", ["date"])

    # (c) fund_transactions: 投信取引（一次データ）。isin→funds.isin FK（誤入力防止）。
    if "fund_transactions" not in tables:
        op.create_table(
            "fund_transactions",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("portfolio_id", sa.Integer(), nullable=False),
            sa.Column("isin", sa.String(), nullable=False),
            sa.Column("side", sa.String(), nullable=False),  # 'buy' / 'sell'
            sa.Column("units", sa.Float(), nullable=False),  # 口数
            sa.Column("price", sa.Float(), nullable=False),  # 約定基準価額（10,000 口あたり円）
            sa.Column("fee", sa.Float(), nullable=True),  # 手数料（avg_cost に含めない）
            sa.Column("traded_at", sa.String(), nullable=False),  # 約定日 'YYYY-MM-DD'
            sa.PrimaryKeyConstraint("id", name="pk_fund_transactions"),
            sa.ForeignKeyConstraint(
                ["portfolio_id"],
                ["portfolios.portfolio_id"],
                name="fk_fund_transactions_portfolio",
            ),
            sa.ForeignKeyConstraint(
                ["isin"], ["funds.isin"], name="fk_fund_transactions_isin_funds"
            ),
        )
        op.create_index("ix_fund_transactions_portfolio", "fund_transactions", ["portfolio_id"])
        op.create_index("ix_fund_transactions_isin", "fund_transactions", ["isin"])

    # (d) fund_holdings: 投信保有（取引から導出）。(portfolio_id,isin) UNIQUE を UPSERT キーに。
    if "fund_holdings" not in tables:
        op.create_table(
            "fund_holdings",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("portfolio_id", sa.Integer(), nullable=False),
            sa.Column("isin", sa.String(), nullable=False),
            sa.Column("units", sa.Float(), nullable=False),  # 導出: Σbuy − Σsell
            sa.Column("avg_cost", sa.Float(), nullable=True),  # 導出: 移動平均取得単価
            sa.PrimaryKeyConstraint("id", name="pk_fund_holdings"),
            sa.ForeignKeyConstraint(
                ["portfolio_id"],
                ["portfolios.portfolio_id"],
                name="fk_fund_holdings_portfolio",
            ),
            sa.ForeignKeyConstraint(["isin"], ["funds.isin"], name="fk_fund_holdings_isin_funds"),
            sa.UniqueConstraint("portfolio_id", "isin", name="uq_fund_holdings_portfolio_isin"),
        )

    # (e) asset_snapshots に fund_value 列を追加（投信評価額バケット）。
    if "fund_value" not in _existing_columns("asset_snapshots"):
        op.add_column("asset_snapshots", sa.Column("fund_value", sa.Float(), nullable=True))


def downgrade() -> None:
    tables = _existing_tables()

    if "fund_value" in _existing_columns("asset_snapshots"):
        op.drop_column("asset_snapshots", "fund_value")

    if "fund_holdings" in tables:
        op.drop_table("fund_holdings")
    if "fund_transactions" in tables:
        op.drop_index("ix_fund_transactions_isin", table_name="fund_transactions")
        op.drop_index("ix_fund_transactions_portfolio", table_name="fund_transactions")
        op.drop_table("fund_transactions")
    if "fund_navs" in tables:
        op.drop_index("ix_fund_navs_date", table_name="fund_navs")
        op.drop_index("ix_fund_navs_isin", table_name="fund_navs")
        op.drop_table("fund_navs")
    if "funds" in tables:
        op.drop_table("funds")
