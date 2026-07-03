"""投資家プロファイル蒸留（profiler 面・distill_investor_profile）のテスト（ADR-082・ループ ④）。

LLM（run_turn）は必ずモック、DB は temp_db（本物 DB に触れない）。取引台帳の SELL でゲートを駆動
する。検証:
- 活動量ゲート＝新規 SELL < 閾値なら run_turn を呼ばず skip・カーソル据え置き／≥閾値で発火。
- run_turn に PROFILER_TOOLSET＋source='profiler' が渡る（toolset 制限）。
- persist＝propose_profile_note が pending 起票・カーソルが最新 SELL の traded_at へ前進。
- LLM ハード失敗は握らず伝播し、カーソルを前進させない。
- 二度目は同じ SELL を新着に数えない（カーソルで有界化）。
- ジョブ＝skip/発火/面未設定は ok=True・ハード失敗は ok=False・NIGHTLY_JOBS の順序。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.advisor import profiler
from app.advisor.tools.registry import PROFILER_TOOLSET
from app.db import repo, schema
from app.db.engine import get_engine
from app.services import investor_behavior


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _new_portfolio(conn: Any) -> int:
    pk = conn.execute(schema.portfolios.insert().values(name="main")).inserted_primary_key
    assert pk is not None
    return int(pk[0])


def _seed_sell(conn: Any, code: str = "7203", date: str = "2026-01-05") -> None:
    pid = _new_portfolio(conn)
    conn.execute(schema.stocks.insert().values(code=code, company_name="銘柄"))  # FK 充足
    conn.execute(
        schema.transactions.insert().values(
            portfolio_id=pid, code=code, side="sell", shares=100, price=95.0, traded_at=date
        )
    )


def _record_run_turn(monkeypatch: pytest.MonkeyPatch, tool_runs: list[dict[str, Any]]):
    """profiler.run_turn を差し替え、呼び出し記録と返す tool_runs を制御する。"""
    calls: list[dict[str, Any]] = []

    async def _fake(messages: Any, **kwargs: Any) -> tuple[str, list[dict[str, Any]]]:
        calls.append(kwargs)
        return "プロファイル所見", tool_runs

    monkeypatch.setattr(profiler, "run_turn", _fake)
    return calls


# ===== 活動量ゲート =====


def test_gate_no_sells_skips_without_llm(monkeypatch: pytest.MonkeyPatch, temp_db: None):
    """新規 SELL が無ければ run_turn を呼ばず skip し、カーソルを据え置く（ADR-082）。"""
    calls = _record_run_turn(monkeypatch, [])
    with get_engine().begin() as conn:
        pid = _new_portfolio(conn)
        conn.execute(schema.stocks.insert().values(code="7203", company_name="銘柄"))  # FK 充足
        conn.execute(
            schema.transactions.insert().values(
                portfolio_id=pid,
                code="7203",
                side="buy",
                shares=100,
                price=90.0,
                traded_at="2026-01-01",
            )
        )

    with get_engine().begin() as conn:
        result = _run(profiler.run_profile_distillation(conn))

    assert result["ran"] is False
    assert "skip" in result["reason"]
    assert calls == []
    with get_engine().connect() as conn:
        assert investor_behavior.profiler_cursor(conn) is None


def test_gate_with_sell_fires_with_profiler_toolset(monkeypatch: pytest.MonkeyPatch, temp_db: None):
    """新規 SELL があれば発火し、run_turn に PROFILER_TOOLSET＋source='profiler' が渡る。"""
    calls = _record_run_turn(monkeypatch, [])
    with get_engine().begin() as conn:
        _seed_sell(conn)

    with get_engine().begin() as conn:
        result = _run(profiler.run_profile_distillation(conn))

    assert result["ran"] is True
    assert result["new_sells"] == 1
    assert len(calls) == 1
    assert calls[0]["tool_names"] == PROFILER_TOOLSET
    assert calls[0]["source"] == "profiler"


# ===== persist ＋ カーソル =====


def test_persist_note_and_advance_cursor(monkeypatch: pytest.MonkeyPatch, temp_db: None):
    """propose_profile_note が pending 起票され、カーソルが最新 SELL の traded_at へ前進する。"""
    tool_runs = [
        {
            "name": "propose_profile_note",
            "args": {"text": "急落で狼狽売りしがち", "evidence": "売り後上昇率 70%（n=5）"},
        }
    ]
    _record_run_turn(monkeypatch, tool_runs)
    with get_engine().begin() as conn:
        _seed_sell(conn, date="2026-01-05")

    with get_engine().begin() as conn:
        result = _run(profiler.run_profile_distillation(conn))

    assert result["ran"] is True
    assert len(result["notes"]) == 1
    with get_engine().connect() as conn:
        rows = repo.list_proposals(conn, status="pending")
        cursor = investor_behavior.profiler_cursor(conn)
    assert len(rows) == 1 and rows[0]["kind"] == "profile_note"
    assert cursor == "2026-01-05"


def test_llm_failure_propagates_and_holds_cursor(monkeypatch: pytest.MonkeyPatch, temp_db: None):
    """LLM ハード失敗は握らず伝播し、カーソルを前進させない（ADR-082/018）。"""

    async def _boom(messages: Any, **_: Any) -> tuple[str, list[dict[str, Any]]]:
        raise RuntimeError("LLM 500")

    monkeypatch.setattr(profiler, "run_turn", _boom)
    with get_engine().begin() as conn:
        _seed_sell(conn)

    with pytest.raises(RuntimeError, match="LLM 500"):  # noqa: PT012 — begin 内で発火を確認
        with get_engine().begin() as conn:
            _run(profiler.run_profile_distillation(conn))

    with get_engine().connect() as conn:
        assert investor_behavior.profiler_cursor(conn) is None


def test_second_run_skips_same_sells(monkeypatch: pytest.MonkeyPatch, temp_db: None):
    """一度蒸留した SELL は次回ゲートの新着に数えない（カーソルで有界化・ADR-082）。"""
    _record_run_turn(monkeypatch, [])
    with get_engine().begin() as conn:
        _seed_sell(conn, date="2026-01-05")

    with get_engine().begin() as conn:
        first = _run(profiler.run_profile_distillation(conn))
    assert first["ran"] is True

    with get_engine().begin() as conn:
        second = _run(profiler.run_profile_distillation(conn))
    assert second["ran"] is False
    assert second["new_sells"] == 0


# ===== ジョブ =====


def test_job_skip_returns_ok_true(monkeypatch: pytest.MonkeyPatch, temp_db: None):
    """ゲート skip は JobResult(ok=True, rows=0)（健全 no-op・ADR-018）。"""
    from app.batch.jobs import distill_investor_profile

    _record_run_turn(monkeypatch, [])  # SELL 無し → skip
    result = distill_investor_profile.run()
    assert result.ok is True
    assert result.rows == 0


def test_job_fire_returns_ok_true_with_rows(monkeypatch: pytest.MonkeyPatch, temp_db: None):
    """発火して傾向メモを起票すれば JobResult(ok=True, rows=1)。"""
    from app.batch.jobs import distill_investor_profile

    _record_run_turn(
        monkeypatch,
        [{"name": "propose_profile_note", "args": {"text": "損切りが遅い", "evidence": "n=5"}}],
    )
    with get_engine().begin() as conn:
        _seed_sell(conn)

    result = distill_investor_profile.run()
    assert result.ok is True
    assert result.rows == 1


def test_job_face_unconfigured_is_silent_skip(monkeypatch: pytest.MonkeyPatch, temp_db: None):
    """profiler 面 未設定は沈黙 skip（ok=True・nag しない・ADR-018/058）。"""
    from app.batch.jobs import distill_investor_profile
    from app.services.llm_config import FaceNotConfiguredError

    async def _raise_face(conn: Any) -> dict[str, object]:
        raise FaceNotConfiguredError("profiler 未設定")

    monkeypatch.setattr(distill_investor_profile, "run_profile_distillation", _raise_face)
    result = distill_investor_profile.run()
    assert result.ok is True
    assert result.rows == 0
    assert "未設定" in result.detail


def test_job_hard_failure_returns_ok_false(monkeypatch: pytest.MonkeyPatch, temp_db: None):
    """設定済みで落ちる（LLM/DB 障害）は ok=False で surface（runner 集約通知・ADR-018）。"""
    from app.batch.jobs import distill_investor_profile

    async def _boom(conn: Any) -> dict[str, object]:
        raise RuntimeError("LLM 500")

    monkeypatch.setattr(distill_investor_profile, "run_profile_distillation", _boom)
    result = distill_investor_profile.run()
    assert result.ok is False
    assert "LLM 500" in result.detail


def test_nightly_jobs_order_profiler_after_experience_before_notify():
    """NIGHTLY_JOBS で distill_experience < distill_investor_profile < notify_digest（ADR-082）。"""
    from app.batch.jobs import NIGHTLY_JOBS

    mods = [f.__module__ for f in NIGHTLY_JOBS]
    i_exp = mods.index("app.batch.jobs.distill_experience")
    i_prof = mods.index("app.batch.jobs.distill_investor_profile")
    i_notify = mods.index("app.batch.jobs.notify_digest")
    assert i_exp < i_prof < i_notify


# ===== digest 行 =====


def test_digest_shows_profile_notes_line(temp_db: None):
    """当夜 profiler が起票した傾向メモがあれば digest に 🪞 の 1 行が出る（ADR-082）。"""
    import json as _json
    from datetime import UTC, datetime

    from app.batch.jobs.notify_digest import build_digest_content

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    with get_engine().begin() as conn:
        repo.insert_proposal(
            conn,
            created_date=today,
            kind="profile_note",
            body=_json.dumps({"text": "損切りが遅い", "evidence": "n=5"}),
            status="pending",
        )

    with get_engine().connect() as conn:
        content = build_digest_content(conn, today)
    assert content is not None
    assert "🪞" in content
    assert "/profile" in content
