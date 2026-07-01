"""persist_notable_picks_from_tool_runs の永続検証（ADR-067・W2）。

tool_runs（submit_notable_stocks の args）から notable_picks を起票する。未知コード drop・dedup・
冪等 UPSERT・空/未呼び出しの扱いを一時 SQLite で検証する。
"""

from __future__ import annotations

from typing import Any

from app.advisor.journaling import persist_notable_picks_from_tool_runs
from app.db import repo
from app.db.engine import get_engine

DATE = "2026-06-05"
STOCK = {
    "code": "72030",
    "company_name": "トヨタ自動車",
    "sector33_code": "3700",
    "sector17_code": "6",
    "market_code": "0111",
    "is_etf": 0,
    "updated_at": "2026-06-02T00:00:00+00:00",
}


def _runs(picks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"name": "submit_notable_stocks", "args": {"picks": picks}}]


def test_persist_known_code_and_drops_unknown(temp_db: None) -> None:
    """既知コードは起票、未知コード（stocks に無い）は drop する（ADR-014/018）。"""
    repo.upsert_stocks([STOCK])
    runs = _runs([{"code": "72030", "reason": "重なりで注目"}, {"code": "99999", "reason": "幻覚"}])
    with get_engine().begin() as conn:
        inserted = persist_notable_picks_from_tool_runs(conn, tool_runs=runs, date=DATE)
    assert inserted == ["72030"]
    with get_engine().connect() as conn:
        picks = repo.list_notable_picks_for_date(conn, DATE)
    assert [p["code"] for p in picks] == ["72030"]
    assert picks[0]["reason"] == "重なりで注目"


def test_persist_dedups_same_code(temp_db: None) -> None:
    """同一 code は 1 度だけ起票する（dedup）。"""
    repo.upsert_stocks([STOCK])
    runs = _runs([{"code": "72030", "reason": "A"}, {"code": "72030", "reason": "B"}])
    with get_engine().begin() as conn:
        inserted = persist_notable_picks_from_tool_runs(conn, tool_runs=runs, date=DATE)
    assert inserted == ["72030"]


def test_persist_idempotent_on_rerun(temp_db: None) -> None:
    """同じ晩に 2 回走っても notable_picks は重複しない（UNIQUE(date,code,source) UPSERT）。"""
    repo.upsert_stocks([STOCK])
    runs = _runs([{"code": "72030", "reason": "初回"}])
    with get_engine().begin() as conn:
        persist_notable_picks_from_tool_runs(conn, tool_runs=runs, date=DATE)
    runs2 = _runs([{"code": "72030", "reason": "再実行で更新"}])
    with get_engine().begin() as conn:
        persist_notable_picks_from_tool_runs(conn, tool_runs=runs2, date=DATE)
    with get_engine().connect() as conn:
        picks = repo.list_notable_picks_for_date(conn, DATE)
    assert len(picks) == 1
    assert picks[0]["reason"] == "再実行で更新"  # UPSERT で reason 更新


def test_persist_skips_malformed_pick_keeps_valid(temp_db: None) -> None:
    """#11: 1 件の不備（reason 欠落）で全落ちせず、有効な pick は残す（per-item グレースフル）。

    旧実装は picks を一括 model_validate し、1 件の ValidationError で全 picks を破棄していた。
    """
    repo.upsert_stocks([STOCK])
    # 2 件目は reason 欠落（不正）。1 件目（有効）は残らねばならない。
    runs = _runs([{"code": "72030", "reason": "重なりで注目"}, {"code": "67580"}])
    with get_engine().begin() as conn:
        inserted = persist_notable_picks_from_tool_runs(conn, tool_runs=runs, date=DATE)
    assert inserted == ["72030"]  # 有効分は残る（全落ちしない）
    with get_engine().connect() as conn:
        picks = repo.list_notable_picks_for_date(conn, DATE)
    assert [p["code"] for p in picks] == ["72030"]


def test_persist_empty_picks(temp_db: None) -> None:
    """picks 空（今夜は注目なし）は何も起票しない（毎回無理に出さない）。"""
    with get_engine().begin() as conn:
        inserted = persist_notable_picks_from_tool_runs(conn, tool_runs=_runs([]), date=DATE)
    assert inserted == []


def test_persist_no_submit_call(temp_db: None) -> None:
    """submit_notable_stocks が呼ばれていなければ何もしない。"""
    with get_engine().begin() as conn:
        inserted = persist_notable_picks_from_tool_runs(
            conn, tool_runs=[{"name": "submit_journal", "args": {}}], date=DATE
        )
    assert inserted == []
