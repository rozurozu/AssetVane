"""news に embedding/embed_model/embedded_at 3 列を追加（ADR-045 段階A）

Revision ID: 0016_news_embedding
Revises: 0015_funds
Create Date: 2026-06-09

ADR-045（ニュース意味検索）。統合コーパス news に貯めた記事を「意味」で過去横断検索できるよう、
各行に embedding ベクトルを持たせる。格納は BLOB 列＋vec_distance_cosine（次元非依存スキャン）で、
vec0 仮想テーブルは使わない（規模が育ったら昇格＝今回は非スコープ）。embed_model 列で
モデル不一致行を再埋め込み対象にする。

追加する列（すべて nullable・未埋め込み/機能オフでも既存運用を壊さない）:
  - embedding   … float32 little-endian の BLOB（vec_distance_cosine が読む・LargeBinary）
  - embed_model … 埋め込みに使ったモデル名（不一致行を再埋め込み対象にするキー）
  - embedded_at … 埋め込み時刻 ISO8601 UTC

採番: 直前 head は 0015_funds。連鎖を直線に保つため down_revision=0015。
冪等性: 既存 DB への再適用に備え、列が無いときだけ add する（0011〜0015 と同方針）。
DB に触れる OS プロセスは FastAPI 1 つ（ADR-005）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016_news_embedding"
down_revision: str | None = "0015_funds"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _existing_columns(table: str) -> set[str]:
    bind = op.get_bind()
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    cols = _existing_columns("news")
    if "embedding" not in cols:
        op.add_column("news", sa.Column("embedding", sa.LargeBinary(), nullable=True))
    if "embed_model" not in cols:
        op.add_column("news", sa.Column("embed_model", sa.String(), nullable=True))
    if "embedded_at" not in cols:
        op.add_column("news", sa.Column("embedded_at", sa.String(), nullable=True))


def downgrade() -> None:
    cols = _existing_columns("news")
    if "embedded_at" in cols:
        op.drop_column("news", "embedded_at")
    if "embed_model" in cols:
        op.drop_column("news", "embed_model")
    if "embedding" in cols:
        op.drop_column("news", "embedding")
