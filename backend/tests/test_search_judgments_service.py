"""search_judgments_for_tool（bookend・JSON-safe・ガード）を担保する（ADR-078・D-1）。

proposal/notable ヒットに proposal_outcomes を合流し horizon 別の実現/超過リターン＋hit（採点済み）
or pending を返すこと、journal はテキストのみ、クエリ 3 文字未満・空母集団でも落ちないことを検証。
"""

from __future__ import annotations

import json
from typing import Any

from app.db.engine import get_engine
from app.db.schema import advisor_journal, notable_picks, proposal_outcomes, proposals
from app.services.judgments import search_judgments_for_tool


def _insert(table: Any, **values: Any) -> int:
    with get_engine().begin() as conn:
        key = conn.execute(table.insert().values(**values)).inserted_primary_key
        assert key is not None
        return int(key[0])


def _run(query: str, **kwargs: Any) -> dict[str, Any]:
    with get_engine().connect() as conn:
        return search_judgments_for_tool(conn, query=query, **kwargs)


def test_proposal_hit_carries_scored_and_pending_outcomes(temp_db: None) -> None:
    """buy 提案ヒットは horizon 20=採点済み・60=pending を outcomes に添える。"""
    pid = _insert(
        proposals,
        created_date="2026-01-06",
        kind="buy",
        body=json.dumps({"code": "7203", "company_name": "トヨタ", "market": "JP"}),
        rationale="好決算を受けて押し目買いが妥当",
        status="pending",
    )
    _insert(
        proposal_outcomes,
        origin_kind="proposal",
        origin_id=pid,
        source="nightly",
        kind="buy",
        code="7203",
        market="JP",
        entry_date="2026-01-06",
        horizon=20,
        as_of_date="2026-02-03",
        realized_return=0.05123,
        excess_return=0.02051,
        benchmark_symbol="^TPX",
        hit=1,
        status="final",
    )
    _insert(
        proposal_outcomes,
        origin_kind="proposal",
        origin_id=pid,
        source="nightly",
        kind="buy",
        code="7203",
        market="JP",
        entry_date="2026-01-06",
        horizon=60,
        status="pending",
    )

    out = _run("好決算")
    assert "reason" not in out
    item = out["items"][0]
    assert item["origin"] == "proposal"
    by_h = {o["horizon"]: o for o in item["outcomes"]}
    assert by_h[20]["status"] == "final"
    assert by_h[20]["realized_return"] == 0.0512  # 4 桁丸め
    assert by_h[20]["excess_return"] == 0.0205
    assert by_h[20]["hit"] is True
    assert by_h[60]["status"] == "pending"
    assert by_h[60]["realized_return"] is None
    assert by_h[60]["hit"] is None
    json.dumps(out)  # Decimal/date を含まない（Tool 返り値の契約）


def test_notable_hit_joins_notable_outcomes(temp_db: None) -> None:
    """注目選別ヒットは origin_kind='notable' の帰結を join し hit は None（非方向）。"""
    nid = _insert(
        notable_picks, date="2026-01-07", code="6758", reason="出来高急増で注目", source="nightly"
    )
    _insert(
        proposal_outcomes,
        origin_kind="notable",
        origin_id=nid,
        source="nightly",
        kind="notable",
        code="6758",
        market="JP",
        entry_date="2026-01-07",
        horizon=20,
        as_of_date="2026-02-04",
        realized_return=0.03,
        benchmark_symbol="^TPX",
        hit=None,
        status="final",
    )
    out = _run("出来高")
    item = out["items"][0]
    assert item["origin"] == "notable"
    assert item["outcomes"][0]["realized_return"] == 0.03
    assert item["outcomes"][0]["hit"] is None


def test_journal_hit_is_text_only(temp_db: None) -> None:
    """journal ヒットは outcomes キーを持たない（テキストのみ）。"""
    _insert(advisor_journal, date="2026-01-05", observations="半導体不足が改善", proposal="")
    out = _run("半導体")
    item = out["items"][0]
    assert item["origin"] == "journal"
    assert "outcomes" not in item
    assert "半導体" in item["text"]


def test_short_query_is_guarded(temp_db: None) -> None:
    """3 文字未満は空＋理由（trigram の下限・error にしない）。"""
    out = _run("株")
    assert out["items"] == []
    assert "3" in out["reason"]


def test_empty_corpus_is_safe(temp_db: None) -> None:
    """母集団ゼロ（ヒットなし）でも error にせず items 空。"""
    out = _run("存在しない語句")
    assert out["items"] == []
    assert "reason" not in out  # 検索は走った（ガードでない）
