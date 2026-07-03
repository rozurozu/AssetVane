"""投資家プロファイル investor_profile の読み書き（ADR-082・テーマ C・★4 自己改善ループ）。

policy（規範）と分離した「記述＝投資家の行動の癖」の単一行ドキュメント。get/upsert とも
policy（repo/advisor.py の get_policy/upsert_policy）と同流儀の 1 行運用（id 固定）。
書き込みは W2（conn を受け取り commit しない・呼び出し側が begin 所有＝backend-repo-pattern）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.schema import investor_profile


def get_investor_profile(conn: Connection) -> dict[str, Any]:
    """investor_profile の 1 行を素の dict で返す（行が無ければ空文字プロファイル・ADR-082）。

    policy と違い None を返さず {"body": "", "updated_at": None} を返す＝注入側（build_messages）は
    body が空なら第 3 層を挿さないだけで分岐が単純になる（初回は /profile で人間が育てるか、夜間
    profiler が承認制メモを積む）。
    """
    row = (
        conn.execute(select(investor_profile).order_by(investor_profile.c.id).limit(1))
        .mappings()
        .first()
    )
    return dict(row) if row else {"body": "", "updated_at": None}


def upsert_investor_profile(conn: Connection, body: str) -> None:
    """investor_profile を 1 行運用で upsert する（id 固定・ADR-082）。

    body は散文プロファイル全文。updated_at は UTC now を焼く。W2（commit しない・呼び出し側が
    begin 所有）。active 文書は人間承認/手編集でのみ書き換わる（ADR-009）。
    """
    now = datetime.now(UTC).isoformat()
    stmt = sqlite_insert(investor_profile).values(id=1, body=body, updated_at=now)
    stmt = stmt.on_conflict_do_update(
        index_elements=["id"],
        set_={"body": stmt.excluded.body, "updated_at": stmt.excluded.updated_at},
    )
    conn.execute(stmt)
