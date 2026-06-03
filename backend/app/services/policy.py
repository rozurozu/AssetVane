"""policy 取得サービス — Phase 3 で policy テーブルが導入されるまでの橋渡し。

Phase 3 で `0006_advisor_state` マイグレーションが policy テーブルを作った後は、
自動的にそちらを読む。Phase 2 は DEFAULT_POLICY の既定値で動かす。
（phase2-spec.md §1「重要な設計判断」・ADR-013・ADR-015）
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Connection, inspect, text

# Phase 2 期間中は policy テーブルが存在しない。
# 将来 Phase 3 で `0006_advisor_state` が入ると自動的にそちらを読む。
# 値はすべて 0..1（比率）。比率系は spec §5 の単位約束に従う。
DEFAULT_POLICY: dict[str, Any] = {
    "risk_tolerance": "中",
    "time_horizon": "中",
    "target_cash_ratio": 0.25,
    "max_position_weight": 0.15,
    "sector_caps": {},
    "target_return": None,
    "no_leverage": 1,
    "exclusions": [],
}


def get_policy(conn: Connection) -> dict[str, Any]:
    """policy を返す。policy テーブルが存在すれば先頭行を読み、なければ DEFAULT_POLICY を返す。

    Phase 3 で `0006_advisor_state` マイグレーションが policy テーブルを作ると、
    自動的にそちらを読む設計（phase2-spec.md §1「重要な設計判断」）。
    テーブル存在チェックは `sqlalchemy.inspect` で行う（ADR-005 DB は FastAPI のみ）。
    """
    insp = inspect(conn)
    if not insp.has_table("policy"):
        return dict(DEFAULT_POLICY)

    # policy テーブルが存在する場合は先頭行を読む（Phase 3 以降）
    row = conn.execute(text("SELECT * FROM policy LIMIT 1")).mappings().first()
    if row is None:
        return dict(DEFAULT_POLICY)

    policy = dict(DEFAULT_POLICY)
    policy.update(dict(row))
    return policy
