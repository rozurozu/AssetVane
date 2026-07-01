"""軸1 夜の分析AI のテスト（phase3-spec.md §5・§10・ADR-018）。

LLM（run_turn）・Discord（notify.error）は必ずモック、DB は temp_db。検証対象:
- submit_journal 経由で observations/proposal が記録され、proposed_policy_change があれば
  proposal が起票されること。
- ②ハード失敗（LLM 例外）は握らず伝播し journal をスキップすること（nightly は通知しない）。
- ③縮退（observations 空）は journal を書かず理由 str を返すこと。
- submit_journal 未呼び出しでも reply 非空なら journal を書く（フォールバック健全）こと。
- run_advisor ジョブが上記を JobResult(ok) に畳み、失敗/縮退は runner 集約通知に乗ること。
- _gather_briefing が監査用 dict を返すこと（handler はモック・#14）。
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


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _stub_briefing(monkeypatch: pytest.MonkeyPatch) -> None:
    """briefing 内の事実取得 handler を軽い dict 返しに差し替える（事実取得を切り離す）。"""

    async def _fake_signals(_args: dict[str, object]) -> dict[str, object]:
        return {"date": "2025-01-01", "signals": []}

    async def _fake_metrics(_args: dict[str, object]) -> dict[str, object]:
        return {"portfolio_id": 1, "sharpe": 1.2}

    async def _fake_overview(_args: dict[str, object]) -> dict[str, object]:
        return {"total_value": 1000.0}

    monkeypatch.setattr(handlers, "handle_get_signals", _fake_signals)
    monkeypatch.setattr(handlers, "handle_get_portfolio_metrics", _fake_metrics)
    monkeypatch.setattr(handlers, "handle_get_asset_overview", _fake_overview)


def test_gather_briefing(monkeypatch: pytest.MonkeyPatch, temp_db: None) -> None:
    """briefing は signals/portfolio_metrics/asset_overview を集約した dict（#14）。"""
    _stub_briefing(monkeypatch)
    briefing = asyncio.run(nightly._gather_briefing())  # 本番と同じ async 経路を直接検証
    assert set(briefing) == {"signals", "portfolio_metrics", "asset_overview"}
    overview = briefing["asset_overview"]
    assert isinstance(overview, dict)
    assert overview["total_value"] == 1000.0


def test_nightly_records_journal_and_proposal(
    monkeypatch: pytest.MonkeyPatch, temp_db: None
) -> None:
    """submit_journal 経由で journal が残り、proposed_policy_change で proposal が起票される。"""
    _stub_briefing(monkeypatch)

    # engine.run_turn（nightly が呼ぶ provider ディスパッチャ）をモックして tool_run を返させる。
    submit_args = {
        "observations": "今日の所見",
        "proposal": "現金比率を上げる",
        "proposed_policy_change": {
            "field": "target_cash_ratio",
            "from": 0.25,
            "to": 0.4,
            "reason": "下落に備える",
        },
    }

    async def _fake_loop(messages: Any, **_: Any) -> tuple[str, list[dict[str, object]]]:
        return "最終応答", [{"name": "submit_journal", "args": submit_args}]

    monkeypatch.setattr(nightly, "run_turn", _fake_loop)

    with get_engine().begin() as conn:
        _run(nightly.run_nightly_advisor(conn))

    with get_engine().connect() as conn:
        journals = repo.list_journal(conn)
        proposals = repo.list_proposals(conn)
        detail = repo.get_journal(conn, journals[0]["id"])
    assert detail is not None

    assert len(journals) == 1
    j = journals[0]
    assert j["source"] == "nightly"
    assert j["observations"] == "今日の所見"
    assert j["proposal"] == "現金比率を上げる"
    assert json.loads(j["proposed_policy_change"])["to"] == 0.4
    # situation_briefing は詳細取得でのみ載る（監査用 JSON）。
    assert json.loads(detail["situation_briefing"])["asset_overview"]["total_value"] == 1000.0

    # proposed_policy_change があるので proposal（policy_change・pending）が 1 件起票される。
    assert len(proposals) == 1
    p = proposals[0]
    assert p["kind"] == "policy_change"
    assert p["status"] == "pending"
    assert p["journal_id"] == j["id"]
    assert json.loads(p["body"])["field"] == "target_cash_ratio"


def test_nightly_policy_snapshot_single_encoded(
    monkeypatch: pytest.MonkeyPatch, temp_db: None
) -> None:
    """夜間経路でも journal の policy_snapshot は単エンコード（ADR-013/018）。

    nightly は repo.get_policy の生行（sector_caps が JSON 文字列）を journaling に渡す。
    journaling が dumps 前に normalize_policy_row で型へ直す（書き込み境界の不変条件）
    ことを、policy 行が存在する状態で直接担保する。
    """
    _stub_briefing(monkeypatch)
    with get_engine().begin() as conn:
        repo.upsert_policy(conn, {"sector_caps": json.dumps({"3700": 0.3})})

    async def _fake_loop(messages: Any, **_: Any) -> tuple[str, list[dict[str, object]]]:
        return "応答", [{"name": "submit_journal", "args": {"observations": "所見"}}]

    monkeypatch.setattr(nightly, "run_turn", _fake_loop)

    with get_engine().begin() as conn:
        _run(nightly.run_nightly_advisor(conn))

    with get_engine().connect() as conn:
        journals = repo.list_journal(conn)
    snapshot = json.loads(journals[0]["policy_snapshot"])
    # 入れ子の JSON 文字列でなく dict のまま読める（二重エンコードしない）。
    assert snapshot["sector_caps"] == {"3700": 0.3}


def test_nightly_without_policy_change_no_proposal(
    monkeypatch: pytest.MonkeyPatch, temp_db: None
) -> None:
    """proposed_policy_change が無ければ journal だけ残り proposal は起票しない。"""
    _stub_briefing(monkeypatch)

    async def _fake_loop(messages: Any, **_: Any) -> tuple[str, list[dict[str, object]]]:
        return "応答", [{"name": "submit_journal", "args": {"observations": "所見のみ"}}]

    monkeypatch.setattr(nightly, "run_turn", _fake_loop)

    with get_engine().begin() as conn:
        _run(nightly.run_nightly_advisor(conn))

    with get_engine().connect() as conn:
        assert len(repo.list_journal(conn)) == 1
        assert repo.list_proposals(conn) == []


def test_nightly_non_dict_policy_change_keeps_journal_no_proposal(
    monkeypatch: pytest.MonkeyPatch, temp_db: None
) -> None:
    """proposed_policy_change が非 dict（文字列）でも journal は残り proposal は起票しない。

    非力なモデルが変更案を markdown 文字列で渡すケースの回帰（頑健性・ADR-018）。
    """
    _stub_briefing(monkeypatch)

    async def _fake_loop(messages: Any, **_: Any) -> tuple[str, list[dict[str, object]]]:
        return "応答", [
            {
                "name": "submit_journal",
                "args": {
                    "observations": "所見",
                    "proposal": "提案",
                    "proposed_policy_change": "- 文字列で来た変更案",
                },
            }
        ]

    monkeypatch.setattr(nightly, "run_turn", _fake_loop)

    with get_engine().begin() as conn:
        _run(nightly.run_nightly_advisor(conn))

    with get_engine().connect() as conn:
        journals = repo.list_journal(conn)
        assert len(journals) == 1
        assert journals[0]["observations"] == "所見"
        assert journals[0]["proposal"] == "提案"
        assert journals[0]["proposed_policy_change"] is None
        assert repo.list_proposals(conn) == []


def test_nightly_multi_field_patch_keeps_journal_no_proposal(
    monkeypatch: pytest.MonkeyPatch, temp_db: None
) -> None:
    """多フィールド patch でも journal は残り、適用不能 proposal は起票しない（U-10 裁定①）。

    弱モデルが複数列同時変更（{field,to} 単一形でない）を渡しても、coerce_policy_change が
    None に倒すため proposal queue に適用不能な提案が入らないことを担保する（ADR-013/018）。
    """
    _stub_briefing(monkeypatch)

    async def _fake_loop(messages: Any, **_: Any) -> tuple[str, list[dict[str, object]]]:
        return "応答", [
            {
                "name": "submit_journal",
                "args": {
                    "observations": "所見",
                    "proposal": "提案",
                    "proposed_policy_change": {
                        "max_position_weight": 0.2,
                        "target_cash_ratio": 0.4,
                    },
                },
            }
        ]

    monkeypatch.setattr(nightly, "run_turn", _fake_loop)

    with get_engine().begin() as conn:
        _run(nightly.run_nightly_advisor(conn))

    with get_engine().connect() as conn:
        journals = repo.list_journal(conn)
        assert len(journals) == 1
        assert journals[0]["observations"] == "所見"
        assert journals[0]["proposed_policy_change"] is None
        assert repo.list_proposals(conn) == []


def test_nightly_llm_failure_propagates_and_skips_journal(
    monkeypatch: pytest.MonkeyPatch, temp_db: None
) -> None:
    """LLM 失敗（例外）は握らず伝播し、journal は書かない（②ハード失敗・ADR-018）。

    新契約: nightly 自身は notify せず、例外をそのまま上位（run_advisor ジョブ）へ伝播する。
    通知は runner 集約が担う（旧挙動「nightly が notify.error を呼ぶ」からの更新）。
    """
    _stub_briefing(monkeypatch)

    async def _failing_loop(messages: Any, **_: Any) -> tuple[str, list[dict[str, object]]]:
        raise RuntimeError("LLM タイムアウト")

    monkeypatch.setattr(nightly, "run_turn", _failing_loop)

    # 例外は握られず伝播する（nightly 内 try/except は撤去済み）。
    with pytest.raises(RuntimeError, match="LLM タイムアウト"):
        with get_engine().begin() as conn:
            _run(nightly.run_nightly_advisor(conn))

    with get_engine().connect() as conn:
        # journal は欠かす（スキップ）。
        assert repo.list_journal(conn) == []

    # nightly 自身は通知経路（notify）を持たない（runner 集約に一本化・ADR-018）。
    assert not hasattr(nightly, "notify")


def test_nightly_empty_response_skips_journal_and_returns_reason(
    monkeypatch: pytest.MonkeyPatch, temp_db: None
) -> None:
    """縮退した晩（例外なし・observations 空）は journal を書かず理由 str を返す（ADR-018）。

    新契約の本丸: 空応答（reply=""・tool_runs=[]）は「実質何もしなかった晩」として失敗扱い。
    journal を書かず、切り分け材料（submit_journal 有無・reply 長・tool_runs 数）を含む理由を返す。
    """
    _stub_briefing(monkeypatch)

    async def _empty_loop(messages: Any, **_: Any) -> tuple[str, list[dict[str, object]]]:
        return "", []

    monkeypatch.setattr(nightly, "run_turn", _empty_loop)

    with get_engine().begin() as conn:
        reason = _run(nightly.run_nightly_advisor(conn))

    assert isinstance(reason, str)
    assert "無応答" in reason  # 縮退の切り分け材料を含む

    with get_engine().connect() as conn:
        # observations 空なので journal は書かない。
        assert repo.list_journal(conn) == []
        assert repo.list_proposals(conn) == []


def test_nightly_reply_without_tool_records_journal(
    monkeypatch: pytest.MonkeyPatch, temp_db: None
) -> None:
    """submit_journal 未呼び出しでも reply 非空なら journal を書く（縮退でない・ADR-018）。

    今回の肝: フォールバックの健全性。Tool を呼ばずとも観察テキストを返したなら正常運用として
    扱い、observations=reply で journal を 1 件残す（proposal は無し）。None を返す＝成功。
    """
    _stub_briefing(monkeypatch)

    async def _reply_only(messages: Any, **_: Any) -> tuple[str, list[dict[str, object]]]:
        return "Tool は呼ばなかったが、市況を踏まえた分析テキストを返す。", []

    monkeypatch.setattr(nightly, "run_turn", _reply_only)

    with get_engine().begin() as conn:
        result = _run(nightly.run_nightly_advisor(conn))

    assert result is None  # 縮退でない＝成功

    with get_engine().connect() as conn:
        journals = repo.list_journal(conn)
        assert len(journals) == 1
        assert journals[0]["observations"].startswith("Tool は呼ばなかったが")
        # submit_journal 不呼び出しなので proposal は起票しない。
        assert repo.list_proposals(conn) == []


def test_run_advisor_job_returns_jobresult(monkeypatch: pytest.MonkeyPatch, temp_db: None) -> None:
    """run_advisor.run は成功時 JobResult(ok=True, rows=1) を返す（asyncio.run で駆動）。"""
    from app.batch.jobs import run_advisor

    async def _noop(conn: Any) -> None:
        return None

    monkeypatch.setattr(run_advisor, "run_nightly_advisor", _noop)
    result = run_advisor.run()
    assert result.name == "run_advisor"
    assert result.ok is True
    assert result.rows == 1


def test_run_advisor_job_hard_failure_returns_ok_false(
    monkeypatch: pytest.MonkeyPatch, temp_db: None
) -> None:
    """run_nightly_advisor が例外を投げると ok=False・detail に「夜AI 実行失敗」（②ハード失敗）。"""
    from app.batch.jobs import run_advisor

    async def _boom(conn: Any) -> None:
        raise RuntimeError("LLM 500")

    monkeypatch.setattr(run_advisor, "run_nightly_advisor", _boom)
    result = run_advisor.run()
    assert result.ok is False
    assert result.rows == 0
    assert "夜AI 実行失敗" in result.detail
    assert "LLM 500" in result.detail


def test_run_advisor_job_degraded_returns_ok_false(
    monkeypatch: pytest.MonkeyPatch, temp_db: None
) -> None:
    """run_nightly_advisor が縮退理由 str を返すと ok=False・detail に理由（③縮退）。"""
    from app.batch.jobs import run_advisor

    async def _degraded(conn: Any) -> str:
        return "夜AI が無応答（observations 空）: ..."

    monkeypatch.setattr(run_advisor, "run_nightly_advisor", _degraded)
    result = run_advisor.run()
    assert result.ok is False
    assert result.rows == 0
    assert "無応答" in result.detail


def test_run_advisor_failure_triggers_runner_aggregate_notify(
    monkeypatch: pytest.MonkeyPatch, temp_db: None
) -> None:
    """ok=False の run_advisor を含む通常完了で runner 集約通知が 1 回鳴る（一本化・ADR-018）。

    nightly→run_advisor→runner の経路を通し、縮退/失敗の通知が runner.notify.error に集約され
    1 度だけ呼ばれることを担保する（ジョブ自身は notify しない＝通知の単一経路）。
    """
    from app.batch import notify, runner, state
    from app.batch.jobs import run_advisor

    state.end()

    # 夜AI を縮退（無応答）させ、run_advisor を ok=False で返させる。
    async def _empty_loop(messages: Any, **_: Any) -> tuple[str, list[dict[str, object]]]:
        return "", []

    _stub_briefing(monkeypatch)
    monkeypatch.setattr(nightly, "run_turn", _empty_loop)

    error_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(notify, "error", lambda t, d: error_calls.append((t, d)))
    monkeypatch.setattr("app.batch.jobs.NIGHTLY_JOBS", [run_advisor.run])

    try:
        runner.run_nightly()
    finally:
        state.end()

    # 集約通知は 1 度だけ。detail に run_advisor の縮退理由が載る。
    assert len(error_calls) == 1
    assert "run_advisor" in error_calls[0][1]
