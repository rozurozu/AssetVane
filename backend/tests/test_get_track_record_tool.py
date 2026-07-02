"""get_track_record Tool（ADR-077）を担保する。

handle_get_track_record が final の proposal_outcomes を source×kind×horizon で正しく集計し、
返り値が JSON-safe（Decimal/date を含まない）であること・フィルタが効くこと・空母集団でも落ちない
ことを、一時 SQLite に seed して検証する（testing-strategy）。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from app.advisor.tools import handlers
from app.db import schema
from app.db.engine import get_engine


def _seed_outcome(conn, **fields: Any) -> None:
    base: dict[str, Any] = {
        "origin_kind": "proposal",
        "source": "nightly",
        "kind": "buy",
        "code": "7203",
        "market": "JP",
        "entry_date": "2026-01-05",
        "horizon": 20,
        "as_of_date": "2026-02-03",
        "realized_return": 0.05,
        "benchmark_symbol": "^TPX",
        "excess_return": 0.02,
        "benchmark_fallback": 0,
        "hit": 1,
        "status": "final",
        "scored_at": "2026-02-03T00:00:00+00:00",
    }
    base.update(fields)
    conn.execute(schema.proposal_outcomes.insert().values(**base))


def test_tool_aggregates_and_json_safe(temp_db: None) -> None:
    """final を集計し hit_rate/平均リターン/件数を source×kind×horizon で返し、json 化できる。"""
    with get_engine().begin() as conn:
        conn.execute(schema.stocks.insert().values(code="7203", company_name="トヨタ"))
        _seed_outcome(conn, origin_id=1, hit=1, realized_return=0.05, excess_return=0.02)
        _seed_outcome(conn, origin_id=2, hit=0, realized_return=-0.01, excess_return=-0.03)
        # 非方向 notable（hit NULL）は別群。
        _seed_outcome(
            conn, origin_kind="notable", origin_id=1, kind="notable", code="6758", hit=None
        )

    out = asyncio.run(handlers.handle_get_track_record({}))

    assert "error" not in out
    buy_grp = next(g for g in out["summary"] if g["kind"] == "buy")
    assert buy_grp["count"] == 2
    assert buy_grp["hit_rate"] == 0.5  # (1+0)/2
    assert buy_grp["source"] == "nightly"
    notable_grp = next(g for g in out["summary"] if g["kind"] == "notable")
    assert notable_grp["hit_rate"] is None  # 非方向は AVG(hit)=NULL
    assert out["recent"]
    json.dumps(out)  # Decimal/date を含まない（Tool 返り値の契約）


def test_tool_filter_by_kind(temp_db: None) -> None:
    """kind フィルタで buy 群だけに絞れる。"""
    with get_engine().begin() as conn:
        _seed_outcome(conn, origin_id=1, kind="buy")
        _seed_outcome(conn, origin_kind="notable", origin_id=1, kind="notable", hit=None)

    out = asyncio.run(handlers.handle_get_track_record({"kind": "buy"}))
    assert [g["kind"] for g in out["summary"]] == ["buy"]


def test_tool_empty_is_safe(temp_db: None) -> None:
    """採点が無くても error にせず空集計を返す（ループを落とさない・ADR-018）。"""
    out = asyncio.run(handlers.handle_get_track_record({}))
    assert out["summary"] == []
    assert out["recent"] == []
    assert out["pending_count"] == 0
