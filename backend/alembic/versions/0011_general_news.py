"""general_news: 一般ニュース台帳（銘柄に紐づかない別系統・ADR-034）

Revision ID: 0011_general_news
Revises: 0010_notifications
Create Date: 2026-06-06

ADR-034（一般ニュースダイジェスト）。dossier_sources（code FK 必須）は個別銘柄ドシエ専用の
まま据え置き、銘柄に紐づかない一般ニュースは **別テーブル** に持つ（code FK を持たず category
列を持つ）。url UNIQUE で再取得の二重取り込みを防ぐ（冪等 UPSERT のキー）。category へ索引を
張り GET /general-news のカテゴリ別グルーピングを速くする。
採番について: 直前 head は 0010_notifications。連鎖を直線に保つため down_revision=0010 とし、
head が 0011 になる。冪等性: 既存 DB への再適用に備えテーブル存在チェックをしてから作成する
（0007/0008/0010 と同方針）。DB に触れる OS プロセスは FastAPI 1 つ（ADR-005）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011_general_news"
down_revision: str | None = "0010_notifications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _existing_tables() -> set[str]:
    bind = op.get_bind()
    return set(sa.inspect(bind).get_table_names())


def upgrade() -> None:
    # general_news（一般ニュース台帳＝ADR-034）。code FK は持たず category 列を持つ。
    # 本文は保存せず summary と url のみ（dossier_sources と同方針・ADR-020 の流儀）。
    if "general_news" not in _existing_tables():
        op.create_table(
            "general_news",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("category", sa.String(), nullable=False),  # ラベル（market/macro/world 等）
            sa.Column("url", sa.String(), nullable=False),  # 取り込み元 URL（本文は保存しない）
            sa.Column("title", sa.String(), nullable=True),
            sa.Column("summary", sa.String(), nullable=True),  # 短い要約（全文は捨てる）
            sa.Column("published_at", sa.String(), nullable=True),  # 発行日 'YYYY-MM-DD'
            sa.Column("fetched_at", sa.String(), nullable=True),  # 取り込み時刻 ISO8601 UTC
            sa.Column("source_type", sa.String(), nullable=True),  # 'news' 等（将来拡張）
            # 取得レベル 'summarized'/'description'/'headline'（NewsAdapter の 3 段フォールバック）
            sa.Column("extraction_status", sa.String(), nullable=True),
            sa.PrimaryKeyConstraint("id", name="pk_general_news"),
            sa.UniqueConstraint("url", name="uq_general_news_url"),  # URL 重複排除
        )
        op.create_index("ix_general_news_category", "general_news", ["category"])


def downgrade() -> None:
    op.drop_index("ix_general_news_category", table_name="general_news")
    op.drop_table("general_news")
