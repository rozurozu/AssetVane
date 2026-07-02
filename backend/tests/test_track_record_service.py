"""track_record service の採点オーケストレーションを担保する（ADR-077・テーマ A）。

pending→final の遷移で realized/excess/hit が埋まること・US 提案が us_daily_quotes＋^SPX で
採点されること・notable が非方向（hit=None）でリターンのみ記録されること・source 導出（journal
由来/NULL→chat）を、一時 SQLite（temp_db）に価格/提案を seed して検証する（本物 DB に触れない）。
horizon は小さな系列で回すため (2,) に monkeypatch する。
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from app.db import repo, schema
from app.db.engine import get_engine
from app.services import track_record


def _seed_quotes(conn, table, key_col: str, code: str, bars: list[tuple[str, float]]) -> None:
    for d, adj in bars:
        conn.execute(
            table.insert().values(**{key_col: code, "date": d, "adj_close": adj, "close": adj})
        )


def _seed_index(conn, symbol: str, bars: list[tuple[str, float]]) -> None:
    for d, close in bars:
        conn.execute(schema.index_quotes.insert().values(symbol=symbol, date=d, close=close))


def _outcomes(market: str | None = None) -> list[dict[str, Any]]:
    with get_engine().connect() as conn:
        stmt = select(schema.proposal_outcomes)
        if market:
            stmt = stmt.where(schema.proposal_outcomes.c.market == market)
        return [dict(r) for r in conn.execute(stmt).mappings().all()]


def test_pending_then_final_transition_jp_buy(temp_db, monkeypatch):
    """JP buy が horizon 未経過で pending→価格追加で final 遷移し realized/excess/hit が埋まる。"""
    monkeypatch.setattr(track_record, "_HORIZONS", (2,))
    with get_engine().begin() as conn:
        conn.execute(schema.stocks.insert().values(code="7203", company_name="トヨタ自動車"))
        # 起点＝2026-01-05、まだ 2 本しか無い → horizon=2 の到達バーが無く pending。
        _seed_quotes(
            conn,
            schema.daily_quotes,
            "code",
            "7203",
            [("2026-01-05", 100.0), ("2026-01-06", 110.0)],
        )
        _seed_index(conn, "^TPX", [("2026-01-05", 200.0), ("2026-01-06", 202.0)])
        repo.insert_proposal(
            conn,
            created_date="2026-01-05",
            kind="buy",
            body='{"code": "7203", "company_name": "トヨタ自動車", "market": "JP"}',
            rationale="好決算",
            status="pending",
        )
        track_record.score_pending_outcomes(conn)

    rows = _outcomes()
    assert len(rows) == 1
    assert rows[0]["status"] == "pending"
    assert rows[0]["realized_return"] is None
    assert rows[0]["hit"] is None

    # 到達バー（2026-01-07=120）＋ベンチを足して再採点 → final。
    with get_engine().begin() as conn:
        _seed_quotes(conn, schema.daily_quotes, "code", "7203", [("2026-01-07", 120.0)])
        _seed_index(conn, "^TPX", [("2026-01-07", 210.0)])
        track_record.score_pending_outcomes(conn)

    rows = _outcomes()
    assert len(rows) == 1  # 冪等 UPSERT（二重化しない）
    r = rows[0]
    assert r["status"] == "final"
    assert r["realized_return"] == pytest.approx(0.20)
    assert r["excess_return"] == pytest.approx(0.20 - 0.05)
    assert r["benchmark_symbol"] == "^TPX"
    assert r["benchmark_fallback"] == 0
    assert r["hit"] == 1  # buy かつ excess>0


def test_us_proposal_scored_with_us_prices_and_spx(temp_db, monkeypatch):
    """market='US' の提案は us_daily_quotes＋^SPX ベンチで採点される（価格源の振り分け）。"""
    monkeypatch.setattr(track_record, "_HORIZONS", (1,))
    with get_engine().begin() as conn:
        conn.execute(schema.us_stocks.insert().values(symbol="AAPL", company_name="Apple"))
        _seed_quotes(
            conn,
            schema.us_daily_quotes,
            "symbol",
            "AAPL",
            [("2026-01-05", 100.0), ("2026-01-06", 90.0)],
        )
        _seed_index(conn, "^SPX", [("2026-01-05", 400.0), ("2026-01-06", 404.0)])
        repo.insert_proposal(
            conn,
            created_date="2026-01-05",
            kind="sell",
            body='{"code": "AAPL", "company_name": "Apple", "market": "US"}',
            rationale="悪化",
            status="pending",
        )
        track_record.score_pending_outcomes(conn)

    rows = _outcomes(market="US")
    assert len(rows) == 1
    r = rows[0]
    assert r["status"] == "final"
    assert r["benchmark_symbol"] == "^SPX"
    assert r["realized_return"] == pytest.approx(-0.10)
    # excess = -0.10 - (404/400 - 1) = -0.10 - 0.01。sell かつ excess<0 → hit。
    assert r["excess_return"] == pytest.approx(-0.11)
    assert r["hit"] == 1


def test_notable_pick_scored_non_directional(temp_db, monkeypatch):
    """notable_pick は JP・非方向で採点され hit=None・リターンのみ記録する。"""
    monkeypatch.setattr(track_record, "_HORIZONS", (1,))
    with get_engine().begin() as conn:
        conn.execute(schema.stocks.insert().values(code="6758", company_name="ソニー"))
        _seed_quotes(
            conn,
            schema.daily_quotes,
            "code",
            "6758",
            [("2026-01-05", 100.0), ("2026-01-06", 105.0)],
        )
        _seed_index(conn, "^TPX", [("2026-01-05", 200.0), ("2026-01-06", 200.0)])
        repo.upsert_notable_pick(
            conn, date="2026-01-05", code="6758", reason="出来高急増", source="nightly"
        )
        track_record.score_pending_outcomes(conn)

    rows = _outcomes()
    assert len(rows) == 1
    r = rows[0]
    assert r["origin_kind"] == "notable"
    assert r["kind"] == "notable"
    assert r["status"] == "final"
    assert r["realized_return"] == pytest.approx(0.05)
    assert r["hit"] is None  # 非方向


def test_source_derivation_from_journal_and_null_falls_back_to_chat(temp_db, monkeypatch):
    """proposal の source は生成元 journal.source を継承し、journal_id NULL は 'chat' に倒す。"""
    monkeypatch.setattr(track_record, "_HORIZONS", (1,))
    with get_engine().begin() as conn:
        conn.execute(schema.stocks.insert().values(code="9984", company_name="ソフトバンクG"))
        conn.execute(schema.stocks.insert().values(code="7203", company_name="トヨタ"))
        _seed_quotes(
            conn,
            schema.daily_quotes,
            "code",
            "9984",
            [("2026-01-05", 100.0), ("2026-01-06", 110.0)],
        )
        _seed_quotes(
            conn,
            schema.daily_quotes,
            "code",
            "7203",
            [("2026-01-05", 100.0), ("2026-01-06", 110.0)],
        )
        _seed_index(conn, "^TPX", [("2026-01-05", 200.0), ("2026-01-06", 200.0)])
        jid = repo.insert_journal(
            conn, date="2026-01-05", source="nightly", observations="夜の所見"
        )
        repo.insert_proposal(
            conn,
            created_date="2026-01-05",
            kind="buy",
            body='{"code": "9984", "company_name": "ソフトバンクG", "market": "JP"}',
            status="pending",
            journal_id=jid,
        )
        repo.insert_proposal(
            conn,
            created_date="2026-01-05",
            kind="buy",
            body='{"code": "7203", "company_name": "トヨタ", "market": "JP"}',
            status="pending",  # journal_id なし → chat
        )
        track_record.score_pending_outcomes(conn)

    by_code = {r["code"]: r for r in _outcomes()}
    assert by_code["9984"]["source"] == "nightly"
    assert by_code["7203"]["source"] == "chat"


def test_get_track_record_aggregates_and_json_safe(temp_db, monkeypatch):
    """get_track_record が source×kind×horizon で集計し、JSON-safe な素の型を返す。"""
    import json

    monkeypatch.setattr(track_record, "_HORIZONS", (1,))
    with get_engine().begin() as conn:
        conn.execute(schema.stocks.insert().values(code="7203", company_name="トヨタ"))
        _seed_quotes(
            conn,
            schema.daily_quotes,
            "code",
            "7203",
            [("2026-01-05", 100.0), ("2026-01-06", 120.0)],
        )
        _seed_index(conn, "^TPX", [("2026-01-05", 200.0), ("2026-01-06", 210.0)])
        repo.insert_proposal(
            conn,
            created_date="2026-01-05",
            kind="buy",
            body='{"code": "7203", "company_name": "トヨタ", "market": "JP"}',
            status="pending",
        )
        track_record.score_pending_outcomes(conn)

    with get_engine().connect() as conn:
        tr = track_record.get_track_record(conn)

    assert tr["summary"], "final 群が集計に出る"
    grp = tr["summary"][0]
    assert grp["source"] == "chat"
    assert grp["kind"] == "buy"
    assert grp["horizon"] == 1
    assert grp["count"] == 1
    assert grp["hit_rate"] == pytest.approx(1.0)
    assert tr["recent"][0]["company_name"] == "トヨタ"
    assert tr["recent"][0]["hit"] is True  # int→bool 化
    # Decimal/date を含まず json 化できる（Tool 返り値の契約・advisor-tool-pattern）。
    json.dumps(tr)


def test_get_track_record_empty_is_safe(temp_db):
    """採点が 1 件も無くても summary/recent 空で落ちない（空母集団の安全既定）。"""
    with get_engine().connect() as conn:
        tr = track_record.get_track_record(conn)
    assert tr["summary"] == []
    assert tr["recent"] == []
    assert tr["pending_count"] == 0
