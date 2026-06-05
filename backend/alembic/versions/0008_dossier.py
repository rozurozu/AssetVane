"""dossier: Stock Dossier の状態テーブル群（Phase 4）

Revision ID: 0008_dossier
Revises: 0007_screening
Create Date: 2026-06-05

Phase 4（phase4-spec.md §2・ADR-020）。watchlist / stock_dossiers / dossier_sources を作成。
- watchlist は夜の巡回対象。UNIQUE(code) で重複監視防止。last_investigated_at は持たず
  stock_dossiers を JOIN して一覧に出す（最終調査日は調査側の真実＝1 か所・§2.1）。
- stock_dossiers は 1 銘柄 1 行の living document（summary_md を更新し続ける）。
- dossier_sources はソース台帳。本文は保存せず summary と url のみ（ADR-020）。UNIQUE(url) で
  再調査の二重取り込みを防ぐ。
採番について: spec §2 は 0007_dossier を想定していたが、先行コミットで 0007_screening が
revision 0007（down_revision=0006_advisor_state）を占有済みのため、連鎖を直線に保つよう
0008_dossier（down_revision=0007_screening）として発行する（head が 0008 になる）。
冪等性: 既存 DB への再適用に備え、テーブルの存在チェックをしてから作成する（0007 と同方針）。
DB に触れる OS プロセスは FastAPI 1 つ（ADR-005）。busy_timeout は engine.py で既設。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_dossier"
down_revision: str | None = "0007_screening"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _existing_tables() -> set[str]:
    bind = op.get_bind()
    return set(sa.inspect(bind).get_table_names())


def upgrade() -> None:
    tables = _existing_tables()

    # watchlist（夜の巡回対象・最終調査日の起点＝§2.1）。UNIQUE(code) で重複監視防止。
    if "watchlist" not in tables:
        op.create_table(
            "watchlist",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("code", sa.String(), nullable=False),  # FK→stocks.code（自分データ・L-7）
            sa.Column("note", sa.String(), nullable=True),  # メモ（任意）
            sa.Column("added_at", sa.String(), nullable=True),  # 追加時刻 ISO8601
            sa.ForeignKeyConstraint(["code"], ["stocks.code"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("code", name="uq_watchlist_code"),
        )
        op.create_index("ix_watchlist_code", "watchlist", ["code"])

    # stock_dossiers（1 銘柄 1 行・living document＝§2.2・ADR-020）。
    if "stock_dossiers" not in tables:
        op.create_table(
            "stock_dossiers",
            sa.Column("code", sa.String(), nullable=False),  # 1 銘柄 1 行（PK）
            sa.Column("summary_md", sa.String(), nullable=True),  # AI 生成の調査要約（markdown）
            sa.Column("key_facts", sa.String(), nullable=True),  # JSON 文字列（出所は Tool の事実）
            sa.Column("last_investigated_at", sa.String(), nullable=True),  # 最終調査時刻 ISO8601
            sa.Column("updated_at", sa.String(), nullable=True),  # 行更新時刻 ISO8601
            sa.ForeignKeyConstraint(["code"], ["stocks.code"]),
            sa.PrimaryKeyConstraint("code", name="pk_stock_dossiers"),
        )

    # dossier_sources（ソース台帳・本文非保存＝§2.3・ADR-020）。UNIQUE(url) で二重取り込み防止。
    if "dossier_sources" not in tables:
        op.create_table(
            "dossier_sources",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("code", sa.String(), nullable=False),  # FK→stocks.code（銘柄のソース一覧）
            sa.Column("source_type", sa.String(), nullable=True),  # 'news'/'disclosure' 等
            sa.Column("url", sa.String(), nullable=False),  # 取り込み元 URL（本文は保存しない）
            sa.Column("title", sa.String(), nullable=True),
            sa.Column("summary", sa.String(), nullable=True),  # 短い要約（全文は捨てる＝ADR-020）
            sa.Column("published_at", sa.String(), nullable=True),  # 発行日 'YYYY-MM-DD'
            sa.Column("processed_at", sa.String(), nullable=True),  # 取り込み時刻 ISO8601
            sa.ForeignKeyConstraint(["code"], ["stocks.code"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("url", name="uq_dossier_sources_url"),
        )
        op.create_index("ix_dossier_sources_code", "dossier_sources", ["code"])


def downgrade() -> None:
    op.drop_index("ix_dossier_sources_code", table_name="dossier_sources")
    op.drop_table("dossier_sources")
    op.drop_table("stock_dossiers")
    op.drop_index("ix_watchlist_code", table_name="watchlist")
    op.drop_table("watchlist")
