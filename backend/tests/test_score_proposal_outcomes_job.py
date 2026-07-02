"""score_proposal_outcomes 夜間ジョブを担保する（ADR-077・テーマ A）。

run() の冪等 UPSERT（2 回で二重化しない）・pending→final の上書き・価格欠落銘柄が混じっても
ok=True で他行を採点することを、一時 SQLite に seed して検証する（testing-strategy）。horizon は
小さな系列で回すため (1,) に monkeypatch する。
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.batch.jobs import score_proposal_outcomes
from app.db import repo, schema
from app.db.engine import get_engine
from app.services import track_record


def _seed_quote(conn, code: str, bars: list[tuple[str, float]]) -> None:
    for d, adj in bars:
        conn.execute(
            schema.daily_quotes.insert().values(code=code, date=d, adj_close=adj, close=adj)
        )


def _outcome_count() -> int:
    with get_engine().connect() as conn:
        return int(
            conn.execute(select(func.count()).select_from(schema.proposal_outcomes)).scalar() or 0
        )


def test_run_is_idempotent(temp_db, monkeypatch):
    """run() を 2 回連続で呼んでも UNIQUE(origin_kind,origin_id,horizon) で二重化しない。"""
    monkeypatch.setattr(track_record, "_HORIZONS", (1,))
    with get_engine().begin() as conn:
        conn.execute(schema.stocks.insert().values(code="7203", company_name="トヨタ"))
        _seed_quote(conn, "7203", [("2026-01-05", 100.0), ("2026-01-06", 110.0)])
        repo.insert_proposal(
            conn,
            created_date="2026-01-05",
            kind="buy",
            body='{"code": "7203", "company_name": "トヨタ", "market": "JP"}',
            status="pending",
        )

    r1 = score_proposal_outcomes.run()
    r2 = score_proposal_outcomes.run()
    assert r1.ok is True
    assert r2.ok is True
    assert _outcome_count() == 1  # horizon 1 本ぶんの 1 行のみ（二重化しない）


def test_run_pending_then_final(temp_db, monkeypatch):
    """horizon 未経過で pending → 価格追加後の run() で同一行が final に上書きされる。"""
    monkeypatch.setattr(track_record, "_HORIZONS", (2,))
    with get_engine().begin() as conn:
        conn.execute(schema.stocks.insert().values(code="7203", company_name="トヨタ"))
        _seed_quote(conn, "7203", [("2026-01-05", 100.0), ("2026-01-06", 110.0)])
        repo.insert_proposal(
            conn,
            created_date="2026-01-05",
            kind="buy",
            body='{"code": "7203", "company_name": "トヨタ", "market": "JP"}',
            status="pending",
        )
    score_proposal_outcomes.run()

    with get_engine().connect() as conn:
        status1 = conn.execute(select(schema.proposal_outcomes.c.status)).scalar()
    assert status1 == "pending"

    with get_engine().begin() as conn:
        _seed_quote(conn, "7203", [("2026-01-07", 121.0)])
    score_proposal_outcomes.run()

    with get_engine().connect() as conn:
        rows = conn.execute(select(schema.proposal_outcomes)).mappings().all()
    assert len(rows) == 1
    assert rows[0]["status"] == "final"
    assert rows[0]["realized_return"] == pytest.approx(0.21)


def test_run_ok_when_a_code_has_no_prices(temp_db, monkeypatch):
    """価格の無い銘柄が混じっても run() は ok=True（pending で保留し他行を採点する）。"""
    monkeypatch.setattr(track_record, "_HORIZONS", (1,))
    with get_engine().begin() as conn:
        conn.execute(schema.stocks.insert().values(code="7203", company_name="トヨタ"))
        conn.execute(schema.stocks.insert().values(code="6758", company_name="ソニー"))
        _seed_quote(conn, "7203", [("2026-01-05", 100.0), ("2026-01-06", 110.0)])
        # 6758 は stocks にあるが daily_quotes 無し → pending（起点バーが無い）。
        repo.insert_proposal(
            conn,
            created_date="2026-01-05",
            kind="buy",
            body='{"code": "7203", "company_name": "トヨタ", "market": "JP"}',
            status="pending",
        )
        repo.insert_proposal(
            conn,
            created_date="2026-01-05",
            kind="buy",
            body='{"code": "6758", "company_name": "ソニー", "market": "JP"}',
            status="pending",
        )

    result = score_proposal_outcomes.run()
    assert result.ok is True
    assert _outcome_count() == 2  # 両方に行ができる（片方は pending）
