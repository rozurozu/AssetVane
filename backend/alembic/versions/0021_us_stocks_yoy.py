"""us_stocks に YoY 中継列（revenue_growth_yoy / earnings_growth_yoy）を追加（ADR-055）

Revision ID: 0021_us_stocks_yoy
Revises: 0020_news_polarity
Create Date: 2026-06-15

ADR-055（米株スクリーナー・統括判断で YoY を活かす方針）。`.info` 提供の YoY 率
（revenueGrowth / earningsGrowth・実値）を us_stocks へ中継し、calc_us_valuation が
us_valuation_snapshots へ転記する。これらの列は 0017_us_equity の us_stocks create_table に
**後から追記**されたが、0017 は `if "us_stocks" not in tables` でガードされているため、追記前に
0017 を適用済みの既存 DB には列が反映されなかった（適用済みマイグレーションの後編集の取りこぼし）。
その結果 repo.list_us_stocks（SELECT が yoy 列を参照）が `no such column` で失敗し、
fetch_us_quotes は銘柄取得段で即 ok=False・fetch_us_fundamentals は upsert_us_stocks が
yoy 列へ書けず全滅していた。本マイグレーションで欠けている列だけを前方追加して解消する。

追加する列（nullable・既存運用を壊さない）:
  - revenue_growth_yoy  … 売上 YoY（`.info.revenueGrowth`・実値の中継）
  - earnings_growth_yoy … 純利益 YoY（`.info.earningsGrowth`・実値の中継）

採番: 直前 head は 0020_news_polarity。連鎖を直線に保つため down_revision=0020。
冪等性: 0017 が yoy 列込みで作成した新規 DB では既に列があるため、列が無いときだけ add する
（0011〜0020 と同方針）。DB に触れる OS プロセスは FastAPI 1 つ（ADR-005）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0021_us_stocks_yoy"
down_revision: str | None = "0020_news_polarity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 追加対象（列名 → 型）。0017 の us_stocks 定義と一致させる（ともに nullable な Float）。
_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    ("revenue_growth_yoy", sa.Float()),
    ("earnings_growth_yoy", sa.Float()),
)


def _existing_columns(table: str) -> set[str]:
    bind = op.get_bind()
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    existing = _existing_columns("us_stocks")
    for name, type_ in _COLUMNS:
        if name not in existing:
            op.add_column("us_stocks", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    existing = _existing_columns("us_stocks")
    for name, _type in _COLUMNS:
        if name in existing:
            op.drop_column("us_stocks", name)
