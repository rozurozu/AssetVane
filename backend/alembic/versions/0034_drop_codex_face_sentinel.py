"""codex の面センチネル provider_id=0 を撤去し NULL に正規化する（ADR-073）

Revision ID: 0034_drop_codex_face_sentinel
Revises: 0033_knowledge_cards_stock_scope
Create Date: 2026-07-02

ADR-073。AI Advisor の codex 経路（provider_id=0 の鍵なし組み込みセンチネル）を撤去した
（ADR-032 を Superseded）。既存の llm_face_config で codex（provider_id=0）を割り当てていた面を
NULL（未設定）へ正規化し、ステールなセンチネル 0 をデータに残さない。列（provider_id）自体は
存続で DDL 変更なし＝データ移行のみ。挙動は移行前後で不変（0 も NULL も resolve_face は
FaceNotConfiguredError＝未設定扱い・ADR-018）で、狙いはデータ衛生。

採番: 直前 head は 0033_knowledge_cards_stock_scope。連鎖を直線に保つため down_revision=0033。
冪等性: WHERE provider_id=0 の UPDATE は再実行しても安全（該当行が無ければ 0 件）。downgrade は
no-op（どの面が codex だったかは復元不能・そもそも codex 経路は撤去済み）。DB に触れる OS
プロセスは FastAPI 1 つ（ADR-005）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0034_drop_codex_face_sentinel"
down_revision: str | None = "0033_knowledge_cards_stock_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # codex(provider_id=0) を割り当てていた面を未設定(NULL)へ正規化する（ADR-073）。
    op.execute(sa.text("UPDATE llm_face_config SET provider_id = NULL WHERE provider_id = 0"))


def downgrade() -> None:
    # 復元不能（どの面が codex だったかは残らない・codex 経路自体が撤去済み）＝no-op。
    pass
