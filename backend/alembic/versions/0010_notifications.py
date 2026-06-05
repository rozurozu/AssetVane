"""notifications: 通知の送信冪等ログ（Phase 6 Signal Beacon）

Revision ID: 0010_notifications
Revises: 0009_news_extraction_and_watchlist_interval
Create Date: 2026-06-05

Phase 6（phase6-spec.md §2・ADR-007/018）。Discord 通知の二重送信防止ログ。
- notify_key は連番ではなく「種別:日付」の自然キー（再実行で同値＝冪等）。
- (notify_key, channel) の複合 PK。channel は当面 'discord'（将来の多チャンネル余地）。
採番について: spec §2 は 0009_notifications を想定していたが、先行コミットで
0009_news_extraction_and_watchlist_interval が revision 0009 を占有済みのため、連鎖を直線に
保つよう 0010_notifications（down_revision=0009_news...）として発行する（head が 0010 になる）。
冪等性: 既存 DB への再適用に備え、テーブルの存在チェックをしてから作成する（0007/0008 と同方針）。
DB に触れる OS プロセスは FastAPI 1 つ（ADR-005）。busy_timeout は engine.py で既設。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010_notifications"
down_revision: str | None = "0009_news_extraction_and_watchlist_interval"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _existing_tables() -> set[str]:
    bind = op.get_bind()
    return set(sa.inspect(bind).get_table_names())


def upgrade() -> None:
    # notifications（送信冪等ログ＝§2・ADR-002/018）。(notify_key, channel) 複合 PK。
    if "notifications" not in _existing_tables():
        op.create_table(
            "notifications",
            sa.Column("notify_key", sa.String(), nullable=False),  # '種別:日付' の自然キー
            sa.Column("channel", sa.String(), nullable=False),  # 'discord'
            sa.Column("sent_at", sa.String(), nullable=True),  # 送信時刻 ISO8601 UTC
            sa.PrimaryKeyConstraint("notify_key", "channel", name="pk_notifications"),
        )


def downgrade() -> None:
    op.drop_table("notifications")
