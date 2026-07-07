"""proposal_outcomes に declared_horizon 列を追加（ADR-091・投資ホライズンの一級化）

Revision ID: 0043_proposal_declared_horizon
Revises: 0042_normalize_us_symbol_notation
Create Date: 2026-07-07

ADR-091（投資ホライズンを一級市民にする）。CORE 要素④が AI に述べさせる想定保有期間
（short/medium/long）を propose_trade が構造化して proposals.body へ持ち、採点時にこの列へ
非正規化コピーする（conviction=0040 と同じ既存パターン）。get_track_record の horizon_calibration
（buy/sell を kind×declared_horizon×horizon で集計）が「宣言した時間軸で実際に報われたか」を
返せるようにする 1 列。

追加する列（nullable・notable/legacy は NULL＝既存運用を壊さない）:
  - declared_horizon … 'short'/'medium'/'long'（NULL=未申告/notable/legacy）。TEXT。

採番: 直前 head は 0042_normalize_us_symbol_notation。連鎖を直線に保つため down_revision=0042。
冪等性: 既存 DB への再適用に備え、列が無いときだけ add する（0040_proposal_conviction と同方針）。
CHECK は張らない: conviction 同様アプリ層で正規化（house style・全 migration で CHECK 未使用）。
proposals 側は body JSON に載せるため列追加なし。DB に触れる OS プロセスは FastAPI 1 つ（ADR-005）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0043_proposal_declared_horizon"
down_revision: str | None = "0042_normalize_us_symbol_notation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _existing_columns(table: str) -> set[str]:
    bind = op.get_bind()
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    if "declared_horizon" not in _existing_columns("proposal_outcomes"):
        op.add_column(
            "proposal_outcomes", sa.Column("declared_horizon", sa.String(), nullable=True)
        )


def downgrade() -> None:
    if "declared_horizon" in _existing_columns("proposal_outcomes"):
        op.drop_column("proposal_outcomes", "declared_horizon")
