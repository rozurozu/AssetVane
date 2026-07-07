"""EDINET DB（edinetdb.jp）接続設定のクエリ（ADR-064・backend-repo-pattern）。

設計の真実: docs/decisions.md ADR-064・docs/data-model.md「EDINET DB 接続設定」節。

api_key と契約プラン名（free/pro）を単一行（id=1）で持つ（jquants_config 同型）。本モジュールは
生の dict を返すだけで、未設定の意味づけ・マスク・プラン正規化は services/edinetdb_config と router
の責務（backend-repo / backend-router）。公式 EDINET（DB の edinet_config・ADR-087）とは別系統。

[書き込みのトランザクション規律] upsert は引数の `conn` 上で execute するだけで commit しない
（W2・jquants_config.py と同じ）。呼び出し側（router）が `with get_engine().begin() as conn:` で
境界を所有し、書き込み→読み戻しを 1 トランザクションに束ねる。
read 関数は conn を受け commit しない。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.schema import edinetdb_config


def get_edinetdb_config(conn: Connection) -> dict[str, Any] | None:
    """edinetdb_config の 1 行を素の dict で返す（無ければ None・ADR-064）。

    api_key は生のまま返す（マスクは router の責務）。プランの正規化は services が担う。
    """
    row = (
        conn.execute(select(edinetdb_config).order_by(edinetdb_config.c.id).limit(1))
        .mappings()
        .first()
    )
    return dict(row) if row else None


def upsert_edinetdb_config(conn: Connection, fields: dict[str, Any]) -> None:
    """edinetdb_config を 1 行運用で upsert する（id 固定・jquants_config 同型・W2・ADR-064）。

    fields は変更したい列のみ（api_key 未送信＝据え置きは呼び出し側が除外して渡す＝write-only）。
    """
    payload = {k: v for k, v in fields.items() if k != "id"}
    payload.setdefault("updated_at", datetime.now(UTC).isoformat())
    stmt = sqlite_insert(edinetdb_config).values(id=1, **payload)
    update_cols = {col: stmt.excluded[col] for col in payload}
    stmt = stmt.on_conflict_do_update(index_elements=["id"], set_=update_cols)
    conn.execute(stmt)
