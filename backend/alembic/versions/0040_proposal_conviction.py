"""proposal_outcomes に conviction 列を追加（ADR-084・確信度キャリブレーション）

Revision ID: 0040_proposal_conviction
Revises: 0039_investor_profile
Create Date: 2026-07-06

ADR-084（判断属性の構造化＋確信度キャリブレーション）。CORE 要素⑤が AI に述べさせる確信度
（高/中/低）を propose_trade が構造化して proposals.body へ持ち、採点時にこの列へ非正規化コピー
する（source/kind/code/market と同じ既存パターン）。get_track_record の calibration（buy/sell を
kind×conviction×horizon で集計）が「高確信ほど当たっているか」を返せるようにする本丸の 1 列。

追加する列（nullable・notable/legacy は NULL＝既存運用を壊さない）:
  - conviction … 'high'/'medium'/'low'（NULL=未申告/notable/legacy）。TEXT。

採番: 直前 head は 0039_investor_profile。連鎖を直線に保つため down_revision=0039。
冪等性: 既存 DB への再適用に備え、列が無いときだけ add する（0020_news_polarity と同方針）。
CHECK は張らない: kind/source/status 同様アプリ層で正規化（house style・全 migration で CHECK 未使用）。
proposals 側は body JSON に載せるため列追加なし。DB に触れる OS プロセスは FastAPI 1 つ（ADR-005）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0040_proposal_conviction"
down_revision: str | None = "0039_investor_profile"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _existing_columns(table: str) -> set[str]:
    bind = op.get_bind()
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    if "conviction" not in _existing_columns("proposal_outcomes"):
        op.add_column("proposal_outcomes", sa.Column("conviction", sa.String(), nullable=True))


def downgrade() -> None:
    if "conviction" in _existing_columns("proposal_outcomes"):
        op.drop_column("proposal_outcomes", "conviction")
