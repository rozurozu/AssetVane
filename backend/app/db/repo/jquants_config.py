"""J-Quants 接続設定のクエリ（ADR-061・backend-repo-pattern）。

設計の真実: docs/decisions.md ADR-061・docs/data-model.md「J-Quants 接続設定」節。

api_key と契約プラン名を単一行（id=1）で持つ（embedding_config 同型）。本モジュールは生の dict を
返すだけで、未設定の意味づけ・マスク・プラン正規化は services/jquants_config と router の責務
（backend-repo / backend-router）。

[書き込みのトランザクション規律] upsert は引数の `conn` 上で execute するだけで commit しない
（W2・llm_config.py と同じ）。呼び出し側（router）が `with get_engine().begin() as conn:` で境界を
所有し、書き込み→読み戻しを 1 トランザクションに束ねる。read 関数は conn を受け commit しない。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.schema import jquants_config


def get_jquants_config(conn: Connection) -> dict[str, Any] | None:
    """jquants_config の 1 行を素の dict で返す（無ければ None・ADR-061）。

    api_key は生のまま返す（マスクは router の責務）。プランの正規化は services が担う。
    """
    row = (
        conn.execute(select(jquants_config).order_by(jquants_config.c.id).limit(1))
        .mappings()
        .first()
    )
    return dict(row) if row else None


def upsert_jquants_config(conn: Connection, fields: dict[str, Any]) -> None:
    """jquants_config を 1 行運用で upsert する（id 固定・embedding_config 同型・W2・ADR-061）。

    fields は変更したい列のみ（api_key 未送信＝据え置きは呼び出し側が除外して渡す＝write-only）。
    """
    payload = {k: v for k, v in fields.items() if k != "id"}
    payload.setdefault("updated_at", datetime.now(UTC).isoformat())
    stmt = sqlite_insert(jquants_config).values(id=1, **payload)
    update_cols = {col: stmt.excluded[col] for col in payload}
    stmt = stmt.on_conflict_do_update(index_elements=["id"], set_=update_cols)
    conn.execute(stmt)
