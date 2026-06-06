"""停止（協調キャンセル）と status/stop エンドポイントを固定する（ADR-036）。

run_nightly はジョブ境界で should_stop を見て残ジョブをスキップし、停止は「正常終了」扱いで
notify.error を鳴らさない。GET /batch/status は idle を返し、POST /batch/stop は idle なら
stopping=false。実ジョブ・実 J-Quants は触らず NIGHTLY_JOBS をフェイクに差し替える。
"""

from __future__ import annotations

import pytest

from app.batch import notify, runner, state
from app.batch.runner import JobResult


@pytest.fixture(autouse=True)
def _reset_state():
    state.end()
    yield
    state.end()


def test_stop_skips_remaining_jobs_and_no_error_notify(
    temp_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """job1 実行中に停止要求 → job2 は走らず、notify.error も呼ばれない（ADR-036）。"""
    ran: list[str] = []

    def job1() -> JobResult:
        ran.append("job1")
        # ジョブ実行中に WebUI から停止が来た状況を模す。
        state.request_stop()
        return JobResult(name="job1", ok=True, rows=0, detail="ok")

    def job2() -> JobResult:
        ran.append("job2")  # 停止済みなので呼ばれてはいけない
        return JobResult(name="job2", ok=True, rows=0, detail="ok")

    error_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(notify, "error", lambda t, d: error_calls.append((t, d)))
    monkeypatch.setattr("app.batch.jobs.NIGHTLY_JOBS", [job1, job2])

    results = runner.run_nightly()

    assert ran == ["job1"]  # job2 はスキップ
    assert [r.name for r in results] == ["job1"]
    assert error_calls == []  # 停止は失敗ではないので通知しない
    assert state.snapshot().running is False  # finally で idle に戻る


def test_failure_still_notifies_when_not_stopped(temp_db, monkeypatch: pytest.MonkeyPatch) -> None:
    """停止していない通常完了では、失敗ジョブがあれば従来どおり 1 度通知する（退行防止）。"""

    def bad() -> JobResult:
        return JobResult(name="bad", ok=False, rows=0, detail="boom")

    error_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(notify, "error", lambda t, d: error_calls.append((t, d)))
    monkeypatch.setattr("app.batch.jobs.NIGHTLY_JOBS", [bad])

    runner.run_nightly()

    assert len(error_calls) == 1


def test_status_endpoint_idle(client) -> None:
    """GET /batch/status は idle なら running=false を返す。"""
    resp = client.get("/batch/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["running"] is False
    assert body["current_job"] is None


def test_stop_endpoint_when_idle(client) -> None:
    """POST /batch/stop は実行中でなければ stopping=false。"""
    resp = client.post("/batch/stop")
    assert resp.status_code == 200
    assert resp.json() == {"stopping": False}
