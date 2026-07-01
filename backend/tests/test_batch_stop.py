"""停止（協調キャンセル）と status/stop エンドポイントを固定する（ADR-036・ADR-070）。

run_nightly はジョブ境界で should_stop を見て残ジョブをスキップし、停止は「正常終了」扱いで
notify.error を鳴らさない。停止フラグはファイル（`data/batch.stop`）が真実源なので、**別プロセスが
立てた停止でも走行中バッチが止まる**（reload/CLI 回帰＝ADR-070）。GET /batch/status は idle を返し、
POST /batch/stop は running ゲート撤廃で常に stopping=true。実ジョブ・実 J-Quants は触らず
NIGHTLY_JOBS をフェイクに差し替える。停止ファイルは tmp に隔離する。
"""

from __future__ import annotations

import pytest

from app.batch import notify, runner, state
from app.batch.runner import JobResult
from app.config import settings


@pytest.fixture(autouse=True)
def _reset_state(tmp_path, monkeypatch):
    """停止ファイルを tmp に隔離し、各テスト前後で idle に戻す（実 data/ に触れない・ADR-070）。"""
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "test.db"))
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


def test_stop_via_file_is_cross_process(temp_db, monkeypatch: pytest.MonkeyPatch) -> None:
    """別プロセスが停止ファイルを立てても走行中バッチが止まる（reload/CLI 回帰・ADR-070）。

    メモリを介さず `batch.stop` を直に touch（= 別プロセスの POST /batch/stop 相当）しても、
    run_jobs のジョブ境界 should_stop がファイルを見て残ジョブをスキップする。
    """
    ran: list[str] = []

    def job1() -> JobResult:
        ran.append("job1")
        # メモリ（_state.stop_requested）を触らず、停止ファイルだけ立てる別プロセスを模す。
        p = state._stop_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()
        return JobResult(name="job1", ok=True, rows=0, detail="ok")

    def job2() -> JobResult:
        ran.append("job2")
        return JobResult(name="job2", ok=True, rows=0, detail="ok")

    error_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(notify, "error", lambda t, d: error_calls.append((t, d)))
    monkeypatch.setattr("app.batch.jobs.NIGHTLY_JOBS", [job1, job2])

    runner.run_nightly()

    assert ran == ["job1"]  # ファイル起点でも job2 はスキップ
    assert error_calls == []


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
    assert body["stop_requested"] is False


def test_stop_endpoint_always_accepts(client) -> None:
    """POST /batch/stop は running ゲート撤廃で idle でも stopping=true（ADR-070）。

    旧実装は idle で false だったが、reload/CLI で前面プロセスの running=false になっても停止を
    届かせるためゲートを撤廃した。stray なファイルは次の begin() が回収する。
    """
    resp = client.post("/batch/stop")
    assert resp.status_code == 200
    assert resp.json() == {"stopping": True}
