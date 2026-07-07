"""公式 EDINET（api.edinet-fsa.go.jp）接続設定のクエリ（ADR-087・backend-repo-pattern）。

設計の真実: docs/decisions.md ADR-087・docs/data-model.md「EDINET（公式）接続設定」節。

Subscription-Key（api_key）を単一行（id=1）で持つ（jquants/edinetdb 同型）。本モジュールは生の
dict を返すだけ。未設定の意味づけ・マスクは services/edinet_config と router の責務。plan 概念は無い
（公式 EDINET は回数クォータ無し）。第三者 edinetdb.jp（edinetdb_config）とは別系統。

[書き込みのトランザクション規律] upsert は引数の `conn` 上で execute するだけで commit しない
（W2・edinetdb_config.py と同じ）。呼び出し側（router）が `with get_engine().begin() as conn:` で
境界を所有し、書き込み→読み戻しを 1 tx に束ねる。read 関数は conn を受け取り commit しない。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.schema import edinet_config


def get_edinet_config(conn: Connection) -> dict[str, Any] | None:
    """edinet_config の 1 行を素の dict で返す（無ければ None・ADR-087）。

    api_key は生のまま返す（マスクは router の責務）。
    """
    row = (
        conn.execute(select(edinet_config).order_by(edinet_config.c.id).limit(1)).mappings().first()
    )
    return dict(row) if row else None


def upsert_edinet_config(conn: Connection, fields: dict[str, Any]) -> None:
    """edinet_config を 1 行運用で upsert する（id 固定・edinetdb_config 同型・W2・ADR-087）。

    fields は変更したい列のみ（api_key 未送信＝据え置きは呼び出し側が除外して渡す＝write-only）。
    """
    payload = {k: v for k, v in fields.items() if k != "id"}
    payload.setdefault("updated_at", datetime.now(UTC).isoformat())
    stmt = sqlite_insert(edinet_config).values(id=1, **payload)
    update_cols = {col: stmt.excluded[col] for col in payload}
    stmt = stmt.on_conflict_do_update(index_elements=["id"], set_=update_cols)
    conn.execute(stmt)
