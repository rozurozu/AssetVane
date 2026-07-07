"""get_position_reviews ツール（ADR-088・#3）の結合テスト。

一時 SQLite に portfolios / stocks / daily_quotes / holdings / proposals をスタブし、handler が
JSON-safe な dict を返し is_delayed を付与すること、Phase 2 で露出し Phase 1 では見えないこと、
例外を {error} に倒すことを検証する（testing-strategy・ネットに出ない）。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from app.advisor.tools import handlers
from app.advisor.tools.registry import openai_tools
from app.db import repo
from app.db.engine import get_engine


def _stock(code: str, name: str) -> dict[str, Any]:
    return {
        "code": code,
        "company_name": name,
        "sector33_code": "3700",
        "sector17_code": "6",
        "market_code": "0111",
        "is_etf": 0,
        "updated_at": "2026-01-01T00:00:00+00:00",
    }


def _seed_flagged_holding() -> int:
    """含み損（-20%）＋記録済み thesis で 1 件 flagged になる保有を作り portfolio_id を返す。"""
    from app.db.schema import holdings, portfolios

    repo.upsert_stocks([_stock("72030", "トヨタ")])
    repo.upsert_daily_quotes(
        [
            {
                "code": "72030",
                "date": "2026-06-05",
                "open": 800.0,
                "high": 800.0,
                "low": 800.0,
                "close": 800.0,
                "volume": 1000.0,
                "adj_close": 800.0,
            }
        ]
    )
    with get_engine().begin() as conn:
        pk = conn.execute(
            portfolios.insert().values(name="メイン", created_at="2026-01-01T00:00:00+00:00")
        ).inserted_primary_key
        assert pk is not None
        pid = pk[0]
        conn.execute(
            holdings.insert().values(portfolio_id=pid, code="72030", shares=100.0, avg_cost=1000.0)
        )
        repo.insert_proposal(
            conn,
            created_date="2026-06-01",
            kind="buy",
            body=json.dumps(
                {
                    "code": "72030",
                    "company_name": "トヨタ",
                    "market": "JP",
                    "invalidation": "崩れたら",
                },
                ensure_ascii=False,
            ),
            rationale="根拠",
            status="pending",
        )
    return int(pid)


def _call(args: dict[str, Any]) -> dict[str, Any]:
    return asyncio.run(handlers.handle_get_position_reviews(args))


def test_returns_json_safe_with_is_delayed(temp_db: None) -> None:
    """flagged 保有が返り、is_delayed が付与され、JSON-safe（handler 契約・ADR-071）。"""
    pid = _seed_flagged_holding()
    out = _call({"portfolio_id": pid})

    assert "error" not in out
    assert "is_delayed" in out  # handler が as_of から付与
    assert out["counts"]["flagged"] == 1
    assert out["reviews"][0]["code"] == "72030"
    json.dumps(out)  # 例外が出なければ JSON-safe


def test_error_is_caught(monkeypatch: Any, temp_db: None) -> None:
    """内部例外は {error} に倒れる（dispatch ループを落とさない）。"""

    def _boom(conn: Any, *, portfolio_id: int | None = None) -> dict[str, Any]:
        raise RuntimeError("DB 障害")

    monkeypatch.setattr(handlers, "build_position_reviews", _boom)
    out = _call({})
    assert "error" in out


def _tool_names(phase: int) -> set[str]:
    return {f["name"] for t in openai_tools(phase) if isinstance((f := t["function"]), dict)}


def test_registry_exposes_tool_at_phase2() -> None:
    """get_position_reviews は Phase 2 で露出し、Phase 1 では見えない。"""
    assert "get_position_reviews" in _tool_names(2)
    assert "get_position_reviews" not in _tool_names(1)
