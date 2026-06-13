"""news に polarity 列を追加（ADR-049/051・能動配信の前提）

Revision ID: 0020_news_polarity
Revises: 0019_us_holdings_fx
Create Date: 2026-06-13

ADR-049（ニュース RAG の線引き＝定性タグのみ・数値スコアは作らない）＋ADR-051（能動配信）。
stock 層ニュースに定性センチメント polarity（'positive'/'negative'/'neutral'・NULL=未判定）を
持たせ、notify_digest の②保有銘柄悪材料アラートが polarity='negative' を拾えるようにする。
tag_news_polarity（embed_news 同型の夜間ジョブ）が stock 層のみ判定する（他層は NULL のまま）。
数値 sentiment_score は持たない（AI に数値を作らせない＝ADR-014/049）。

追加する列（nullable・未判定/他層でも既存運用を壊さない）:
  - polarity … 定性タグ TEXT（'positive'/'negative'/'neutral'・NULL=未判定）

採番: 直前 head は 0019_us_holdings_fx。連鎖を直線に保つため down_revision=0019。
冪等性: 既存 DB への再適用に備え、列が無いときだけ add する（0011〜0019 と同方針）。
索引は張らない: ②の抽出は code IN ＋ polarity 絞りで既存 ix_news_code が効く（ADR-051）。
DB に触れる OS プロセスは FastAPI 1 つ（ADR-005）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0020_news_polarity"
down_revision: str | None = "0019_us_holdings_fx"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _existing_columns(table: str) -> set[str]:
    bind = op.get_bind()
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    if "polarity" not in _existing_columns("news"):
        op.add_column("news", sa.Column("polarity", sa.String(), nullable=True))


def downgrade() -> None:
    if "polarity" in _existing_columns("news"):
        op.drop_column("news", "polarity")
