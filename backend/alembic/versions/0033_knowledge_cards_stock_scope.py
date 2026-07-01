"""knowledge_cards に銘柄スコープ（market/code）を追加（ADR-062 追補・銘柄粒度の知識軸）

Revision ID: 0033_knowledge_cards_stock_scope
Revises: 0032_notable_picks
Create Date: 2026-07-02

ADR-062 追補。知識カードに「1 カード 1 銘柄」の銘柄スコープを足す。個別銘柄特有の知見
（アノマリー等）を level='stock' として厳密に紐づける器。ドシエ（stock_dossiers・毎晩上書きの
揮発的な事実要約）とは別で、蓄積する解釈的知見はこちらに置く。同定は market+code、注入は当面
code 一致（FocusRef は market を運ばない）。code 付きは exact-match でだけ注入し汎用の意味検索
プールからは除外する（他銘柄漏れ防止）。

追加するもの（docs/data-model.md と鏡写し）:
  - knowledge_cards.market … 'JP'/'US'（銘柄ノートのとき・非銘柄カードは NULL）。
  - knowledge_cards.code   … 銘柄コード（JP 5 桁 / US ティッカー・非銘柄カードは NULL）。
  - ix_knowledge_cards_market_code … exact-match 注入を速くする複合インデックス。

採番: 直前 head は 0032_notable_picks。連鎖を直線に保つため down_revision=0032。backfill なし
（既存カードは非銘柄＝NULL のまま）。冪等性: 列/インデックスが無いときだけ追加する
（0011〜0032 と同方針・SQLite は ADD COLUMN 可）。DB に触れる OS プロセスは FastAPI 1 つ
（ADR-005）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0033_knowledge_cards_stock_scope"
down_revision: str | None = "0032_notable_picks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _existing_columns(table: str) -> set[str]:
    bind = op.get_bind()
    return {col["name"] for col in sa.inspect(bind).get_columns(table)}


def _existing_indexes(table: str) -> set[str]:
    bind = op.get_bind()
    return {ix["name"] for ix in sa.inspect(bind).get_indexes(table)}


def upgrade() -> None:
    cols = _existing_columns("knowledge_cards")
    if "market" not in cols:
        op.add_column("knowledge_cards", sa.Column("market", sa.String(), nullable=True))
    if "code" not in cols:
        op.add_column("knowledge_cards", sa.Column("code", sa.String(), nullable=True))
    if "ix_knowledge_cards_market_code" not in _existing_indexes("knowledge_cards"):
        op.create_index("ix_knowledge_cards_market_code", "knowledge_cards", ["market", "code"])


def downgrade() -> None:
    if "ix_knowledge_cards_market_code" in _existing_indexes("knowledge_cards"):
        op.drop_index("ix_knowledge_cards_market_code", table_name="knowledge_cards")
    cols = _existing_columns("knowledge_cards")
    if "code" in cols:
        op.drop_column("knowledge_cards", "code")
    if "market" in cols:
        op.drop_column("knowledge_cards", "market")
