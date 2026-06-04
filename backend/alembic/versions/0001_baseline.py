"""baseline: stocks / daily_quotes（Phase 0）

Revision ID: 0001_baseline
Revises:
Create Date: 2026-06-02

Phase 0 時点のスキーマ。`create_all/drop_all` で作るが schema.py と必ず一致させる。
既存 DB（create_all で先に作られた状態）に対して upgrade しても CREATE は IF NOT EXISTS
相当で**非破壊**。以降のスキーマ変更（financials/signals 等）は別リビジョンに刻む。

【Phase 0 の2表に凍結】（Phase 1・spec §2）:
metadata 全体の create_all にすると、schema.py に後続 Phase の表（fetch_meta/signals 等）を
足した瞬間に **fresh DB で 0001 が全表を作ってしまい、続く 0002/0003 が "table already exists"
で落ちる**。これを避けるため、0001 は `tables=[stocks, daily_quotes]` だけを明示して凍結する。
追加表はそれぞれ専用リビジョン（0002_fetch_meta・0003_signals）で op.create_table する。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from app.db.schema import daily_quotes, stocks

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 0001 が責任を持つのは Phase 0 の2表のみ（上記 docstring の凍結理由）。
_TABLES = [stocks, daily_quotes]


def upgrade() -> None:
    # checkfirst=True で「CREATE は IF NOT EXISTS 相当」を実装でも保証（docstring の非破壊約束）。
    # Phase 0 に create_all で作られ alembic 未 stamp の既存 DB へ upgrade head しても、既存
    # stocks/daily_quotes と衝突せず no-op で通り、0001 を stamp して 0002 以降へ進める。
    for table in _TABLES:
        table.create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    # 対称に checkfirst=True（DROP は IF EXISTS 相当）で、未作成テーブルがあっても落ちない。
    for table in reversed(_TABLES):
        table.drop(bind=op.get_bind(), checkfirst=True)
