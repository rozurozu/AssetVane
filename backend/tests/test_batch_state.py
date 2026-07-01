"""バッチ実行状態と停止フラグの遷移を固定する（ADR-036・停止のファイル化＝ADR-070）。

status（running/current_job/started_at/full_backfill）はメモリ、停止フラグ（stop_requested）は
`data/batch.stop` ファイルが真実源（ADR-070）。begin は前回の停止要求を必ず消し、request_stop は
running ゲートなしで常にファイルを書く。should_stop はメモリでなくファイルを見る（reload/CLI で
別プロセスから止められても走行中バッチに届く）。停止ファイルは実 data/ でなく tmp に隔離する。
[[batch-pattern]]。
"""

from __future__ import annotations

import pytest

from app.batch import state
from app.config import settings


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    """停止ファイルを tmp に隔離し、各テスト前後で idle に戻す（実 data/ に触れない・ADR-070）。"""
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "test.db"))
    state.end()
    yield
    state.end()


def test_idle_by_default() -> None:
    s = state.snapshot()
    assert s.running is False
    assert s.current_job is None
    assert s.started_at is None
    assert s.stop_requested is False


def test_begin_sets_running_and_resets_stop() -> None:
    state.request_stop()  # idle でも今はファイルを書く（stray な停止要求）
    assert state.should_stop() is True
    state.begin(full_backfill=True)
    s = state.snapshot()
    assert s.running is True
    assert s.full_backfill is True
    assert s.started_at is not None
    assert s.stop_requested is False  # begin が stray を回収する（ADR-070 の不変条件）
    assert state.should_stop() is False


def test_set_current_job() -> None:
    state.begin(full_backfill=False)
    state.set_current_job("fetch_quotes")
    assert state.snapshot().current_job == "fetch_quotes"


def test_request_stop_always_writes_file() -> None:
    """running ゲート撤廃＝idle でも受理して True・ファイルを書く（ADR-070）。"""
    # idle でも受理する（旧実装は False だった）。
    assert state.request_stop() is True
    assert state.should_stop() is True
    # begin が stray をクリアし、走行中に改めて要求しても受理する。
    state.begin(full_backfill=False)
    assert state.should_stop() is False
    assert state.request_stop() is True
    assert state.should_stop() is True


def test_should_stop_reads_file_not_memory() -> None:
    """should_stop はメモリでなく停止ファイルを見る（別プロセス起点でも効く・ADR-070）。"""
    p = state._stop_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    # 別プロセスの request_stop 相当: メモリを介さずファイルだけ立てる。
    p.touch()
    assert state.should_stop() is True
    p.unlink()
    assert state.should_stop() is False


def test_begin_clears_stray_stop_file() -> None:
    """idle 中に立った停止要求は次の begin() が回収し、新バッチを即殺しない（ADR-070）。"""
    state.request_stop()
    assert state.should_stop() is True
    state.begin(full_backfill=False)
    assert state.should_stop() is False


def test_stop_aware_stops_after_request() -> None:
    """stop_aware は停止要求が立った次の反復先頭で打ち切る（最内ループ停止・ADR-036 追補）。"""
    seen: list[int] = []
    for i in state.stop_aware(range(5)):
        seen.append(i)
        if i == 2:
            state.request_stop()  # 3 個目の処理中に停止要求
    assert seen == [0, 1, 2]  # 次の反復先頭で should_stop→break（4,5 は yield されない）


def test_end_returns_to_idle_and_clears_stop() -> None:
    state.begin(full_backfill=True)
    state.set_current_job("calc_signals")
    state.request_stop()
    state.end()
    s = state.snapshot()
    assert s.running is False
    assert s.current_job is None
    assert s.full_backfill is False
    assert s.stop_requested is False
    assert state.should_stop() is False  # end が停止ファイルも消す
