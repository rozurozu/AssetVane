"""news_extraction_and_watchlist_interval: ニュース取得レベル列＋銘柄別調査間隔

Revision ID: 0009_news_extraction_and_watchlist_interval
Revises: 0008_dossier
Create Date: 2026-06-05

NewsAdapter 実装（P1）と銘柄別調査 cadence（P2）の土台（計画・ADR-020/ADR-033）。
- dossier_sources.extraction_status: 本文取得の成否を 'summarized'/'description'/'headline' で
  記録する列（3 段フォールバックのどの段まで届いたか）。nullable で追加（既存行は NULL）。
- watchlist.interval_days: 銘柄ごとの調査間隔（既定 21・stale 起点）。固定 N=3／stale=21 を廃し
  per-row 間隔に作り替える（ADR-033）。既存行は 21 で backfill し現状挙動を維持する。
SQLite は ALTER TABLE ADD COLUMN を素で受けるため batch_alter_table は不要（add_column は直接可）。
ただし drop_column は SQLite で table 再構築を伴うため batch_alter_table を使う。
DB に触れる OS プロセスは FastAPI 1 つ（ADR-005）。busy_timeout は engine.py で既設。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009_news_extraction_and_watchlist_interval"
down_revision: str | None = "0008_dossier"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # dossier_sources.extraction_status（取得レベル記録・本文取得の成否＝計画/ADR-020）。
    op.add_column(
        "dossier_sources",
        sa.Column("extraction_status", sa.String(), nullable=True),
    )

    # watchlist.interval_days（銘柄ごとの調査間隔・既定 21＝ADR-033）。
    op.add_column(
        "watchlist",
        sa.Column("interval_days", sa.Integer(), nullable=True),
    )
    # 既存行は 21 で backfill（固定 stale=21 と同じ挙動を維持）。
    op.execute("UPDATE watchlist SET interval_days = 21 WHERE interval_days IS NULL")


def downgrade() -> None:
    # SQLite は drop_column で table 再構築が要るため batch_alter_table で囲む。
    with op.batch_alter_table("watchlist") as batch:
        batch.drop_column("interval_days")
    with op.batch_alter_table("dossier_sources") as batch:
        batch.drop_column("extraction_status")
