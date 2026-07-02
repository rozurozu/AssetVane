"""search_judgments Tool（ADR-078）の handler を担保する。

handle_search_judgments が judgment_fts を横断想起し JSON-safe な items を返すこと・origin/code
フィルタ・query 欠落は {"error"}・空母集団でも落ちないことを、一時 SQLite に seed して検証する
（handler を asyncio.run で直接叩く＝test_get_track_record_tool 同型・testing-strategy）。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from app.advisor.tools import handlers
from app.db.engine import get_engine
from app.db.schema import advisor_journal, proposals


def _insert(table: Any, **values: Any) -> int:
    with get_engine().begin() as conn:
        key = conn.execute(table.insert().values(**values)).inserted_primary_key
        assert key is not None
        return int(key[0])


def _seed() -> None:
    _insert(
        advisor_journal,
        date="2026-01-05",
        observations="半導体不足が改善し増益",
        proposal="押し目買い",
    )
    _insert(
        proposals,
        created_date="2026-01-06",
        kind="buy",
        body=json.dumps({"code": "7203", "company_name": "トヨタ", "market": "JP"}),
        rationale="好決算で押し目買いが妥当",
        status="pending",
    )


def test_tool_returns_items_json_safe(temp_db: None) -> None:
    """handler が items を返し JSON 化できる（Decimal/date 非混入）。"""
    _seed()
    out = asyncio.run(handlers.handle_search_judgments({"query": "半導体"}))
    assert "error" not in out
    assert out["items"][0]["origin"] == "journal"
    json.dumps(out)


def test_tool_filters_by_origin_and_code(temp_db: None) -> None:
    """origin/code フィルタで proposal に絞れる。"""
    _seed()
    by_origin = asyncio.run(
        handlers.handle_search_judgments({"query": "押し目", "origin": "proposal"})
    )
    assert [i["origin"] for i in by_origin["items"]] == ["proposal"]

    by_code = asyncio.run(handlers.handle_search_judgments({"query": "押し目", "code": "7203"}))
    assert [i["code"] for i in by_code["items"]] == ["7203"]


def test_tool_missing_query_is_error(temp_db: None) -> None:
    """query 欠落は {"error": ...}（境界で弾く・ループを落とさない）。"""
    out = asyncio.run(handlers.handle_search_judgments({}))
    assert "error" in out


def test_tool_empty_is_safe(temp_db: None) -> None:
    """ヒットなしでも error にせず items 空。"""
    _seed()
    out = asyncio.run(handlers.handle_search_judgments({"query": "存在しない語句"}))
    assert out["items"] == []
