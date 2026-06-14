"""repo パッケージ共通の最小ヘルパ（UPSERT・embedding パック）。ADR-002/045。"""

from __future__ import annotations

import struct
from typing import Any

from sqlalchemy import Table
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.engine import get_engine


def _upsert(table: Table, rows: list[dict[str, Any]], index_elements: list[str]) -> int:
    """rows を UPSERT する。衝突キー以外の列を EXCLUDED で更新（冪等）。"""
    if not rows:
        return 0
    stmt = sqlite_insert(table)
    update_cols = {
        col.name: stmt.excluded[col.name] for col in table.columns if col.name not in index_elements
    }
    stmt = stmt.on_conflict_do_update(index_elements=index_elements, set_=update_cols)
    with get_engine().begin() as conn:
        conn.execute(stmt, rows)
    return len(rows)


def pack_embedding(vector: list[float]) -> bytes:
    """embedding（list[float]）を float32 little-endian の BLOB に詰める（ADR-045）。

    sqlite-vec の vec_distance_cosine が読む格納形式。検索クエリのベクトル化にも同じ関数を使い、
    格納側と問い合わせ側のバイト表現を一致させる（次元非依存スキャン）。
    """
    return struct.pack(f"<{len(vector)}f", *vector)
