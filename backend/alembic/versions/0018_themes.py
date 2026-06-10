"""テーマタグ 3 テーブルを追加（themes / stock_themes / company_descriptions・段階A）

Revision ID: 0018_themes
Revises: 0017_us_equity
Create Date: 2026-06-10

ADR-050 改訂・ADR-056（テーマタグは全ユニバース（JP＋US）を実在テキストに grounded で
事前タグ付けする）。業種コードをまたぐテーマ（"AI需要"・"防衛"・"円安メリット" 等）で銘柄を
束ねる。テーマは定性タグで数値ではない（ADR-014）。

追加するテーブル（docs/data-model.md「テーマタグ」節と鏡写し）:
  - themes               … テーマ語彙の目録（name PK・JP＋US 横断のグローバル語彙・
                           embedding は語彙 reconcile 用＝ADR-045 流用・near_duplicate_of は
                           重複候補フラグで自動マージしない）
  - stock_themes         … 銘柄×theme 台帳（UNIQUE(market,code,theme_name)・cross-FK なし＝
                           signals と同じ生データ流儀・source 列なし＝UPSERT＋last_seen_at の
                           時間窓 prune で 2 書き手共存＝ADR-050 の意図的決定）
  - company_descriptions … 事業説明の実在テキスト（UNIQUE(market,code)・grounded タガーの
                           信号源。source/doc_id/disclosed_date はテキストの provenance）

採番: 直前 head は 0017_us_equity。連鎖を直線に保つため down_revision=0017。
冪等性: 既存 DB への再適用に備え、テーブルが無いときだけ create する（0011〜0017 と同方針）。
DB に触れる OS プロセスは FastAPI 1 つ（ADR-005）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0018_themes"
down_revision: str | None = "0017_us_equity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _existing_tables() -> set[str]:
    bind = op.get_bind()
    return set(sa.inspect(bind).get_table_names())


def upgrade() -> None:
    tables = _existing_tables()

    if "themes" not in tables:
        op.create_table(
            "themes",
            sa.Column("name", sa.String(), primary_key=True),
            sa.Column("embedding", sa.LargeBinary(), nullable=True),
            sa.Column("embed_model", sa.String(), nullable=True),
            sa.Column("first_seen_at", sa.String()),
            sa.Column("near_duplicate_of", sa.String(), nullable=True),
        )

    if "stock_themes" not in tables:
        op.create_table(
            "stock_themes",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("market", sa.String(), nullable=False),
            sa.Column("code", sa.String(), nullable=False),
            sa.Column("theme_name", sa.String(), nullable=False),
            sa.Column("first_assigned_at", sa.String()),
            sa.Column("last_seen_at", sa.String()),
            sa.UniqueConstraint(
                "market", "code", "theme_name", name="uq_stock_themes_market_code_theme"
            ),
        )
        op.create_index("ix_stock_themes_market_code", "stock_themes", ["market", "code"])
        op.create_index("ix_stock_themes_theme_name", "stock_themes", ["theme_name"])

    if "company_descriptions" not in tables:
        op.create_table(
            "company_descriptions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("market", sa.String(), nullable=False),
            sa.Column("code", sa.String(), nullable=False),
            sa.Column("source", sa.String()),
            sa.Column("description_text", sa.String()),
            sa.Column("disclosed_date", sa.String(), nullable=True),
            sa.Column("doc_id", sa.String(), nullable=True),
            sa.Column("fetched_at", sa.String()),
            sa.UniqueConstraint("market", "code", name="uq_company_descriptions_market_code"),
        )


def downgrade() -> None:
    tables = _existing_tables()
    if "company_descriptions" in tables:
        op.drop_table("company_descriptions")
    if "stock_themes" in tables:
        op.drop_index("ix_stock_themes_theme_name", table_name="stock_themes")
        op.drop_index("ix_stock_themes_market_code", table_name="stock_themes")
        op.drop_table("stock_themes")
    if "themes" in tables:
        op.drop_table("themes")
