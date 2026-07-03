"""経験蒸留（reviewer 面・distill_experience）のテスト（ADR-081・テーマ B・自己改善ループ ④）。

LLM（run_turn）は必ずモック、DB は temp_db（本物 DB に触れない）。採点済み outcome は価格系列を
経ず proposal_outcomes に直接 seed する（採点ロジックは test_track_record_service が担保済み）。
検証:
- 活動量ゲート＝新規 final < 閾値なら run_turn を呼ばず skip・カーソル据え置き／≥閾値で発火。
- 素材整形＝count≥min_samples の傾向バケットだけ提示・新規 final を起点根拠で bookend。
- persist＝propose_card の source が 'reviewer' に強制・status=draft。
- カーソル＝成功で最新 scored_at へ前進・skip/LLM 失敗で不変。
- tool allowlist＝reviewer に見える Tool は REVIEWER_TOOLSET の 5 本のみ・propose_trade 等は不在。
- ジョブ＝skip/発火/面未設定は ok=True・ハード失敗は ok=False・NIGHTLY_JOBS の順序。
- digest＝当夜 reviewer draft があれば 1 行出る。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.advisor import reviewer
from app.advisor.tools.registry import REVIEWER_TOOLSET, openai_tools
from app.db import repo
from app.db.engine import get_engine
from app.services import experience


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _seed_final(
    conn: Any,
    *,
    origin_kind: str = "proposal",
    origin_id: int,
    source: str = "nightly",
    kind: str = "buy",
    code: str = "7203",
    market: str = "JP",
    entry_date: str = "2026-01-05",
    horizon: int = 20,
    realized: float = 0.10,
    excess: float | None = 0.05,
    hit: int | None = 1,
    scored_at: str,
) -> None:
    """final 済み outcome を 1 行直接 seed する（価格系列を経ない・採点は別テストが担保）。"""
    repo.upsert_proposal_outcome(
        conn,
        origin_kind=origin_kind,
        origin_id=origin_id,
        source=source,
        kind=kind,
        code=code,
        market=market,
        entry_date=entry_date,
        horizon=horizon,
        entry_priced_date=entry_date,
        entry_price=100.0,
        as_of_date="2026-02-01",
        exit_price=100.0 * (1 + realized),
        realized_return=realized,
        benchmark_symbol="^TPX",
        excess_return=excess,
        benchmark_fallback=0,
        hit=hit,
        status="final",
        scored_at=scored_at,
    )


def _record_run_turn(monkeypatch: pytest.MonkeyPatch, tool_runs: list[dict[str, Any]]):
    """reviewer.run_turn を差し替え、呼び出し記録と返す tool_runs を制御する。"""
    calls: list[dict[str, Any]] = []

    async def _fake(messages: Any, **kwargs: Any) -> tuple[str, list[dict[str, Any]]]:
        calls.append(kwargs)
        return "レビュー所見", tool_runs

    monkeypatch.setattr(reviewer, "run_turn", _fake)
    return calls


# ===== 活動量ゲート =====


def test_gate_below_threshold_skips_without_llm(monkeypatch: pytest.MonkeyPatch, temp_db: None):
    """新規 final が閾値未満なら run_turn を呼ばず skip し、カーソルを据え置く（ADR-081）。"""
    calls = _record_run_turn(monkeypatch, [])
    with get_engine().begin() as conn:
        # 既定 reviewer_min_new_finals=3 未満（2 件）。
        _seed_final(conn, origin_id=1, scored_at="2026-02-01T00:00:00")
        _seed_final(conn, origin_id=2, scored_at="2026-02-01T00:00:01")

    with get_engine().begin() as conn:
        result = _run(reviewer.run_experience_distillation(conn))

    assert result["ran"] is False
    assert "skip" in result["reason"]
    assert calls == []  # LLM を呼んでいない
    with get_engine().connect() as conn:
        assert experience.reviewer_cursor(conn) is None  # カーソル据え置き


def test_gate_at_threshold_fires(monkeypatch: pytest.MonkeyPatch, temp_db: None):
    """新規 final が閾値以上なら発火し、run_turn に reviewer toolset が渡る（ADR-081）。"""
    calls = _record_run_turn(monkeypatch, [])
    with get_engine().begin() as conn:
        for i in range(3):
            _seed_final(conn, origin_id=i + 1, scored_at=f"2026-02-01T00:00:0{i}")

    with get_engine().begin() as conn:
        result = _run(reviewer.run_experience_distillation(conn))

    assert result["ran"] is True
    assert result["new_finals"] == 3
    assert len(calls) == 1
    # reviewer は最小 toolset だけ見せる（多重防御・ADR-081）。
    assert calls[0]["tool_names"] == REVIEWER_TOOLSET
    assert calls[0]["source"] == "reviewer"


# ===== 素材整形 =====


def test_material_only_high_sample_buckets(temp_db: None):
    """傾向として提示するのは count≥min_samples のバケットだけ（過学習足切り・ADR-081）。"""
    with get_engine().begin() as conn:
        # (nightly,buy,20) を 3 件＝傾向として残る。
        for i in range(3):
            _seed_final(conn, origin_id=i + 1, scored_at=f"2026-02-01T00:00:0{i}")
        # (chat,sell,20) を 1 件＝サンプル不足で除外。
        _seed_final(
            conn,
            origin_id=99,
            source="chat",
            kind="sell",
            hit=1,
            realized=-0.05,
            excess=-0.06,
            scored_at="2026-02-01T00:00:09",
        )

    with get_engine().connect() as conn:
        material = experience.build_distillation_material(conn, min_samples=3)

    keys = {(b["source"], b["kind"], b["horizon"]) for b in material["patterns"]}
    assert ("nightly", "buy", 20) in keys
    assert ("chat", "sell", 20) not in keys  # count=1 は傾向にしない


def test_material_bookends_rationale_and_numbers(temp_db: None):
    """新規 final は起点根拠（rationale）→採点数値で bookend され、数値は verbatim（ADR-081）。"""
    with get_engine().begin() as conn:
        pid = repo.insert_proposal(
            conn,
            created_date="2026-01-05",
            kind="buy",
            body='{"code": "7203", "company_name": "トヨタ", "market": "JP"}',
            rationale="好決算で上方修正",
            status="pending",
        )
        _seed_final(
            conn, origin_id=pid, realized=0.20, excess=0.15, scored_at="2026-02-01T00:00:00"
        )

    with get_engine().connect() as conn:
        material = experience.build_distillation_material(conn, min_samples=1)
    text = experience.format_material_for_prompt(material)

    assert "好決算で上方修正" in text  # 起点根拠（頭）
    assert "+20.00%" in text  # 実現リターン（尾・Python 計算の verbatim）
    assert "+15.00%" in text  # 超過リターン


# ===== persist（source 強制）＋カーソル =====


def test_persist_forces_reviewer_source_and_advances_cursor(
    monkeypatch: pytest.MonkeyPatch, temp_db: None
):
    """propose_card の source が 'reviewer' に強制され draft 起票・カーソルが scored_at へ前進。"""
    # LLM が source='chat' と偽っても reviewer に上書きされる（source を信用しない・ADR-081）。
    tool_runs = [
        {
            "name": "propose_card",
            "args": {
                "body": "buy 提案は 20 営業日で対ベンチ超過が伸びにくい傾向（n=3）。",
                "title": "短期 buy は対ベンチで慎重に",
                "source": "chat",
            },
        }
    ]
    _record_run_turn(monkeypatch, tool_runs)
    with get_engine().begin() as conn:
        for i in range(3):
            _seed_final(conn, origin_id=i + 1, scored_at=f"2026-02-01T00:00:0{i}")

    with get_engine().begin() as conn:
        result = _run(reviewer.run_experience_distillation(conn))

    assert result["ran"] is True
    assert len(result["drafts"]) == 1

    with get_engine().connect() as conn:
        card = repo.get_knowledge_card(conn, result["drafts"][0])
        cursor = experience.reviewer_cursor(conn)
    assert card is not None
    assert card["source"] == "reviewer"  # LLM の 'chat' でなく決定論で reviewer
    assert card["status"] == "draft"  # 活性化は人間（ADR-009）
    assert cursor == "2026-02-01T00:00:02"  # 最新 final scored_at へ前進


def test_llm_failure_propagates_and_holds_cursor(monkeypatch: pytest.MonkeyPatch, temp_db: None):
    """LLM ハード失敗は握らず伝播し、カーソルを前進させない（ADR-081/018）。"""

    async def _boom(messages: Any, **_: Any) -> tuple[str, list[dict[str, Any]]]:
        raise RuntimeError("LLM 500")

    monkeypatch.setattr(reviewer, "run_turn", _boom)
    with get_engine().begin() as conn:
        for i in range(3):
            _seed_final(conn, origin_id=i + 1, scored_at=f"2026-02-01T00:00:0{i}")

    with pytest.raises(RuntimeError, match="LLM 500"):
        with get_engine().begin() as conn:
            _run(reviewer.run_experience_distillation(conn))

    with get_engine().connect() as conn:
        assert experience.reviewer_cursor(conn) is None  # 前進していない


def test_second_run_after_review_skips_same_finals(monkeypatch: pytest.MonkeyPatch, temp_db: None):
    """一度レビューした final は次回ゲートの新着に数えない（カーソルで有界化・ADR-081）。"""
    _record_run_turn(monkeypatch, [])
    with get_engine().begin() as conn:
        for i in range(3):
            _seed_final(conn, origin_id=i + 1, scored_at=f"2026-02-01T00:00:0{i}")

    with get_engine().begin() as conn:
        first = _run(reviewer.run_experience_distillation(conn))
    assert first["ran"] is True

    # 追加の新規 final なし → カーソル超の新着 0 件 → skip。
    with get_engine().begin() as conn:
        second = _run(reviewer.run_experience_distillation(conn))
    assert second["ran"] is False
    assert second["new_finals"] == 0


# ===== tool allowlist =====


def test_openai_tools_allowlist_restricts_to_reviewer_toolset(temp_db: None):
    """openai_tools(allow=REVIEWER_TOOLSET) は 5 本だけ・propose_trade/submit_journal は不在。"""
    tools = openai_tools(available_phase=7, allow=REVIEWER_TOOLSET)
    names = {t["function"]["name"] for t in tools}  # type: ignore[index]
    assert names == set(REVIEWER_TOOLSET)
    assert "propose_trade" not in names
    assert "submit_journal" not in names
    assert "submit_notable_stocks" not in names
    # 制限なしなら propose_trade は見える（回帰＝allowlist が既定挙動を変えない）。
    all_names = {t["function"]["name"] for t in openai_tools(available_phase=7)}  # type: ignore[index]
    assert "propose_trade" in all_names


# ===== ジョブ =====


def test_job_skip_returns_ok_true(monkeypatch: pytest.MonkeyPatch, temp_db: None):
    """ゲート skip は JobResult(ok=True, rows=0)（健全 no-op・ADR-018）。"""
    from app.batch.jobs import distill_experience

    _record_run_turn(monkeypatch, [])
    with get_engine().begin() as conn:
        _seed_final(conn, origin_id=1, scored_at="2026-02-01T00:00:00")  # 1 件 < 閾値

    result = distill_experience.run()
    assert result.ok is True
    assert result.rows == 0


def test_job_fire_returns_ok_true_with_rows(monkeypatch: pytest.MonkeyPatch, temp_db: None):
    """発火して draft を作れば JobResult(ok=True, rows=1)。"""
    from app.batch.jobs import distill_experience

    _record_run_turn(monkeypatch, [{"name": "propose_card", "args": {"body": "傾向の教訓（n=3）"}}])
    with get_engine().begin() as conn:
        for i in range(3):
            _seed_final(conn, origin_id=i + 1, scored_at=f"2026-02-01T00:00:0{i}")

    result = distill_experience.run()
    assert result.ok is True
    assert result.rows == 1


def test_job_face_unconfigured_is_silent_skip(monkeypatch: pytest.MonkeyPatch, temp_db: None):
    """reviewer 面 未設定は沈黙 skip（ok=True・nag しない・ADR-018/058）。"""
    from app.batch.jobs import distill_experience
    from app.services.llm_config import FaceNotConfiguredError

    async def _raise_face(conn: Any) -> dict[str, object]:
        raise FaceNotConfiguredError("reviewer 未設定")

    monkeypatch.setattr(distill_experience, "run_experience_distillation", _raise_face)
    result = distill_experience.run()
    assert result.ok is True
    assert result.rows == 0
    assert "未設定" in result.detail


def test_job_hard_failure_returns_ok_false(monkeypatch: pytest.MonkeyPatch, temp_db: None):
    """設定済みで落ちる（LLM/DB 障害）は ok=False で surface（runner 集約通知・ADR-018）。"""
    from app.batch.jobs import distill_experience

    async def _boom(conn: Any) -> dict[str, object]:
        raise RuntimeError("LLM 500")

    monkeypatch.setattr(distill_experience, "run_experience_distillation", _boom)
    result = distill_experience.run()
    assert result.ok is False
    assert result.rows == 0
    assert "LLM 500" in result.detail


def test_nightly_jobs_order_distill_after_score_before_notify():
    """NIGHTLY_JOBS で score_proposal_outcomes < distill_experience < notify_digest（ADR-081）。"""
    from app.batch.jobs import NIGHTLY_JOBS

    mods = [f.__module__ for f in NIGHTLY_JOBS]
    i_score = mods.index("app.batch.jobs.score_proposal_outcomes")
    i_distill = mods.index("app.batch.jobs.distill_experience")
    i_notify = mods.index("app.batch.jobs.notify_digest")
    assert i_score < i_distill < i_notify


# ===== digest 行 =====


def test_digest_shows_reviewer_draft_line(temp_db: None):
    """当夜 reviewer draft があれば digest に 1 行出る（ADR-081・Q9）。"""
    from datetime import UTC, datetime

    from app.batch.jobs.notify_digest import build_digest_content

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    with get_engine().begin() as conn:
        repo.insert_knowledge_card_tx(
            conn,
            title="短期 buy は慎重に",
            body="傾向の教訓",
            status="draft",
            source="reviewer",
        )

    with get_engine().connect() as conn:
        content = build_digest_content(conn, today)
    assert content is not None
    assert "🗂" in content
    assert "/cards" in content
