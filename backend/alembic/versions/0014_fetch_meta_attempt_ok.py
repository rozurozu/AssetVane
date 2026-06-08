"""fetch_meta に last_attempt_ok 列を追加（取得試行の成否・ADR-018）

Revision ID: 0014_fetch_meta_attempt_ok
Revises: 0013_news_corpus
Create Date: 2026-06-08

夜間バッチ fetch_index は「試行した全シンボルが失敗したときだけ ok=False」に変更し、一部失敗
（例: Free プランで取れない ^TPX）では成功扱いにしてバッチ失敗アラートを鳴らさない（ADR-018）。
代わりに「今回取れなかった指数」を朝の digest に情報行で出すため、シンボル別に直近の取得試行の
成否を記録する列 last_attempt_ok（1=成功 / 0=失敗 / NULL=未試行）を fetch_meta に足す。
失敗記録時は last_fetched_date（差分取得の再開点）は据え置く（repo.mark_fetch_attempt_failed）。
採番: 直前 head は 0013_news_corpus。連鎖を直線に保つため down_revision=0013。冪等性: 既存 DB への
再適用に備え、列が無いときだけ追加する（0007/0008/0010/0011/0012 と同方針）。
DB に触れる OS プロセスは FastAPI 1 つ（ADR-005）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014_fetch_meta_attempt_ok"
down_revision: str | None = "0013_news_corpus"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMN = "last_attempt_ok"


def _existing_columns(table: str) -> set[str]:
    bind = op.get_bind()
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    if _COLUMN not in _existing_columns("fetch_meta"):
        op.add_column("fetch_meta", sa.Column(_COLUMN, sa.Integer(), nullable=True))


def downgrade() -> None:
    if _COLUMN in _existing_columns("fetch_meta"):
        op.drop_column("fetch_meta", _COLUMN)
