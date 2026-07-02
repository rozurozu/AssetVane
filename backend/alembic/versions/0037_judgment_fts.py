"""判断ログ横断想起の FTS5 索引（judgment_fts）＋同期トリガを追加（ADR-078・D-1）

Revision ID: 0037_judgment_fts
Revises: 0036_proposal_outcomes
Create Date: 2026-07-02

ADR-078（★2 D-1）。夜AI・チャットの過去判断（帯域 n=1 問題）を、埋め込み/LLM 不要の全文検索で
横断想起できるようにする。永続済みの判断ログ 3 ソース（advisor_journal / proposals /
notable_picks）を trigram トークナイザ（CJK 部分一致）で索引する統合スタンドアロン仮想表
judgment_fts と、各基底表 → judgment_fts の同期トリガ（9 本）を追加する。生チャットは索引しない
（ADR-029 の会話揮発を守る）。

このリポジトリ初の生 DDL（CREATE VIRTUAL TABLE / CREATE TRIGGER）migration。DDL の**単一真実源**は
app/db/fts.py に置き、create_schema()（テスト経路）と本 migration の両方が同じ関数を呼ぶ
（同じ DDL を二重に書かない＝ADR-078 の Q3=A）。upgrade は rebuild_judgment_fts で仮想表＋トリガを
作り、既存 journal/proposals/notable_picks を backfill する（本番は既存行あり・新規/テストは空で
no-op）。

採番: 直前 head は 0036_proposal_outcomes。連鎖を直線に保つため down_revision=0036。
DB に触れる OS プロセスは FastAPI 1 つ（ADR-005）。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from app.db.fts import drop_judgment_fts, rebuild_judgment_fts

revision: str = "0037_judgment_fts"
down_revision: str | None = "0036_proposal_outcomes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ensure（仮想表＋トリガ・IF NOT EXISTS）＋ backfill（既存判断ログを流し込む）。
    rebuild_judgment_fts(op.get_bind())


def downgrade() -> None:
    drop_judgment_fts(op.get_bind())
