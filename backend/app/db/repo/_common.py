"""repo パッケージ共通の最小ヘルパ（UPSERT・embedding パック）。ADR-002/045。"""

from __future__ import annotations

import struct
from typing import Any

from sqlalchemy import Table
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.engine import get_engine


def _upsert(
    table: Table,
    rows: list[dict[str, Any]],
    index_elements: list[str],
    *,
    partial: bool = False,
) -> int:
    """rows を UPSERT する（冪等）。

    partial=False（既定）: 衝突キー以外の**全列**を EXCLUDED で更新する。単一の書き手が毎回全列を
    作るテーブル向け（daily_quotes/financials/signals 等）。この場合、行に含まれない列は EXCLUDED
    が既定値=NULL に解決され既存値を潰すので、全列を毎回渡す前提でのみ使う。

    partial=True: rows に**実在する列の和集合だけ**を EXCLUDED 更新し、行に無い列は既存値を温存する
    （ADR-064 #2 / ADR-070・#1/#7/#8）。valuation_snapshots の DSO/DIO・stocks.edinet_code のように
    「主要列を書く UPSERT」と「担当列だけ書く別ジョブの UPDATE」が同じ行を分担するテーブルで、全列
    EXCLUDED 更新が他ジョブの焼いた列を毎晩 NULL に潰すのを防ぐ。rows はキーが揃っている前提（同一
    ビルダー由来＝executemany で列集合が一定）。更新対象がキー列だけなら DO NOTHING で握る（冪等）。
    """
    if not rows:
        return 0
    if partial:
        present = {k for r in rows for k in r}
        update_names = [c for c in present if c not in index_elements]
    else:
        update_names = [col.name for col in table.columns if col.name not in index_elements]
    stmt = sqlite_insert(table)
    if update_names:
        set_ = {name: stmt.excluded[name] for name in update_names}
        stmt = stmt.on_conflict_do_update(index_elements=index_elements, set_=set_)
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=index_elements)
    with get_engine().begin() as conn:
        conn.execute(stmt, rows)
    return len(rows)


def pack_embedding(vector: list[float]) -> bytes:
    """embedding（list[float]）を float32 little-endian の BLOB に詰める（ADR-045）。

    sqlite-vec の vec_distance_cosine が読む格納形式。検索クエリのベクトル化にも同じ関数を使い、
    格納側と問い合わせ側のバイト表現を一致させる（次元非依存スキャン）。
    """
    return struct.pack(f"<{len(vector)}f", *vector)
