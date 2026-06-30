"""edinetdb.jp 接続設定（edinetdb_config）＋ stocks.edinet_code を追加（ADR-064）

Revision ID: 0030_edinetdb_config
Revises: 0029_financials_guidance
Create Date: 2026-06-30

ADR-064。業績の質 #2（売掛/在庫の質）の着工調査で、手元の EDINET キーが公式 EDINET
（api.edinet-fsa.go.jp）ではなく第三者サービス edinetdb.jp（base https://edinetdb.jp/v1・認証
X-API-Key・キー接頭辞 edb_）のものと判明した。#2 の JP 側は edinetdb.jp の構造化財務
（trade_receivables/inventories/revenue/gross_profit）を使う。その接続設定（api_key/plan）を
env でなく DB（単一行）＋/settings の WebUI で管理する＝jquants_config と同型（ADR-061）。
あわせて sec_code↔edinet_code を解決して焼くため stocks に edinet_code 列を足す。

追加するもの（docs/data-model.md と鏡写し）:
  - edinetdb_config … edinetdb.jp 接続（単一行・api_key 平文・GET でマスク・plan 既定 "free"）。
  - stocks.edinet_code … edinetdb.jp の銘柄キー（nullable・夜間に /companies 一覧から解決）。

採番: 直前 head は 0029_financials_guidance。連鎖を直線に保つため down_revision=0029。
データ移行はしない（env からの自動移行もしない＝/settings で再設定する＝ADR-061 と同じ割り切り）。
冪等性: テーブル/列が無いときだけ作る（0024/0029 と同方針）。DB に触れる OS プロセスは
FastAPI 1 つ（ADR-005）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0030_edinetdb_config"
down_revision: str | None = "0029_financials_guidance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _existing_tables() -> set[str]:
    bind = op.get_bind()
    return set(sa.inspect(bind).get_table_names())


def _existing_columns(table: str) -> set[str]:
    bind = op.get_bind()
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    if "edinetdb_config" not in _existing_tables():
        op.create_table(
            "edinetdb_config",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("api_key", sa.String(), nullable=False, server_default=""),
            sa.Column("plan", sa.String(), nullable=False, server_default="free"),
            sa.Column("updated_at", sa.String()),
        )
    if "edinet_code" not in _existing_columns("stocks"):
        op.add_column("stocks", sa.Column("edinet_code", sa.String(), nullable=True))


def downgrade() -> None:
    if "edinet_code" in _existing_columns("stocks"):
        op.drop_column("stocks", "edinet_code")
    if "edinetdb_config" in _existing_tables():
        op.drop_table("edinetdb_config")
