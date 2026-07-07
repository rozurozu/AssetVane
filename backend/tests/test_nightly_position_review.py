"""軸1 夜の分析AI への保有棚卸し注入（ADR-089・#5）のテスト。

run_turn / briefing handler をモックし temp_db で検証する:
- 前提崩れの疑いがある保有があると、instruction に棚卸しセクションが載り、flagged code が
  candidate_codes（銘柄ノート注入）に入ること。
- 疑いが無いと『保有の前提崩れ: なし』が載ること（空注入しない）。
- 夜AI が propose_trade(action='sell') を返すと、既存経路で sell 提案（pending）が起票されること
  （承認制で約定しない＝ADR-009）。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.advisor import nightly
from app.advisor.tools import handlers
from app.db import repo
from app.db.engine import get_engine


def _stub_briefing(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_signals(_args: dict[str, object]) -> dict[str, object]:
        return {"date": "2026-06-05", "signals": []}

    async def _fake_metrics(_args: dict[str, object]) -> dict[str, object]:
        return {"portfolio_id": 1}

    async def _fake_overview(_args: dict[str, object]) -> dict[str, object]:
        return {"total_value": 1000.0}

    monkeypatch.setattr(handlers, "handle_get_signals", _fake_signals)
    monkeypatch.setattr(handlers, "handle_get_portfolio_metrics", _fake_metrics)
    monkeypatch.setattr(handlers, "handle_get_asset_overview", _fake_overview)


def _capture_messages(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """run_turn をモックし instruction（messages）と candidate_codes を捕まえる。"""
    captured: dict[str, Any] = {"messages": None, "candidate_codes": None}

    async def _fake_loop(messages: Any, **_: Any) -> tuple[str, list[dict[str, object]]]:
        captured["messages"] = messages
        return "応答", [{"name": "submit_journal", "args": {"observations": "棚卸し所見"}}]

    async def _fake_cards(_focus: Any, *, candidate_codes: list[str] | None = None) -> list[str]:
        captured["candidate_codes"] = candidate_codes
        return []

    monkeypatch.setattr(nightly, "run_turn", _fake_loop)
    monkeypatch.setattr(nightly, "load_card_texts_for_injection", _fake_cards)
    return captured


def _instruction_blob(messages: Any) -> str:
    """messages（Message/dict の list）の content を連結して 1 本の文字列にする。"""
    out: list[str] = []
    for m in messages:
        content = getattr(m, "content", None)
        if content is None and isinstance(m, dict):
            content = m.get("content")
        if content:
            out.append(str(content))
    return "\n".join(out)


def _seed_flagged_holding() -> None:
    """含み損（-30%）＋記録済み thesis で 1 件 flagged になる保有を作る（既定 PF）。"""
    from app.db.schema import holdings, portfolios

    repo.upsert_stocks(
        [
            {
                "code": "72030",
                "company_name": "トヨタ",
                "sector33_code": "3700",
                "sector17_code": "6",
                "market_code": "0111",
                "is_etf": 0,
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        ]
    )
    repo.upsert_daily_quotes(
        [
            {
                "code": "72030",
                "date": "2026-06-05",
                "open": 700.0,
                "high": 700.0,
                "low": 700.0,
                "close": 700.0,
                "volume": 1000.0,
                "adj_close": 700.0,
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


def test_flagged_holding_injected_into_instruction(
    monkeypatch: pytest.MonkeyPatch, temp_db: None
) -> None:
    """前提崩れの疑いがあると棚卸しが instruction に載り、flagged code が注入対象に入る。"""
    _stub_briefing(monkeypatch)
    captured = _capture_messages(monkeypatch)
    _seed_flagged_holding()

    with get_engine().begin() as conn:
        asyncio.run(nightly.run_nightly_advisor(conn))

    blob = _instruction_blob(captured["messages"])
    assert "保有の前提崩れの疑い" in blob
    assert "72030" in blob
    assert "72030" in (captured["candidate_codes"] or [])


def test_no_flagged_holding_shows_none(monkeypatch: pytest.MonkeyPatch, temp_db: None) -> None:
    """前提崩れの疑いが無ければ『保有の前提崩れ: なし』が載る（空注入しない）。"""
    _stub_briefing(monkeypatch)
    captured = _capture_messages(monkeypatch)

    with get_engine().begin() as conn:
        asyncio.run(nightly.run_nightly_advisor(conn))

    blob = _instruction_blob(captured["messages"])
    assert "保有の前提崩れ: なし" in blob


def test_sell_proposal_from_thesis_break_persists(
    monkeypatch: pytest.MonkeyPatch, temp_db: None
) -> None:
    """夜AI が propose_trade(sell) を返すと sell 提案（pending）が起票される（ADR-009）。"""
    _stub_briefing(monkeypatch)
    _seed_flagged_holding()

    async def _fake_loop(messages: Any, **_: Any) -> tuple[str, list[dict[str, object]]]:
        return "応答", [
            {"name": "submit_journal", "args": {"observations": "前提が崩れた"}},
            {
                "name": "propose_trade",
                "args": {
                    "action": "sell",
                    "code": "72030",
                    "reason": "記録した前提が崩れた",
                    "invalidation": "崩れた",
                },
            },
        ]

    async def _fake_cards(_focus: Any, *, candidate_codes: list[str] | None = None) -> list[str]:
        return []

    monkeypatch.setattr(nightly, "run_turn", _fake_loop)
    monkeypatch.setattr(nightly, "load_card_texts_for_injection", _fake_cards)

    with get_engine().begin() as conn:
        asyncio.run(nightly.run_nightly_advisor(conn))

    with get_engine().connect() as conn:
        sells = [p for p in repo.list_proposals(conn, status="pending") if p["kind"] == "sell"]
    assert len(sells) == 1
    assert json.loads(sells[0]["body"])["code"] == "72030"
