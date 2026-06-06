"""バッチ実行状態（メモリ singleton）の遷移を固定する（ADR-036・batch/state.py）。

begin で running・started_at・full_backfill が立ち stop_requested は初期化される。set_current_job
で現ジョブが映る。request_stop は走行中だけ受理し、end で idle へ戻る。[[batch-pattern]]。
"""

from __future__ import annotations

import pytest

from app.batch import state


@pytest.fixture(autouse=True)
def _reset_state():
    """各テスト前後で idle に戻す（モジュール global を汚さない）。"""
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
    state.request_stop()  # idle なので受理されない（前回の残り stop を持ち越さない確認の布石）
    state.begin(full_backfill=True)
    s = state.snapshot()
    assert s.running is True
    assert s.full_backfill is True
    assert s.started_at is not None
    assert s.stop_requested is False  # begin で必ず初期化される


def test_set_current_job() -> None:
    state.begin(full_backfill=False)
    state.set_current_job("fetch_quotes")
    assert state.snapshot().current_job == "fetch_quotes"


def test_request_stop_only_when_running() -> None:
    # idle では受理しない。
    assert state.request_stop() is False
    assert state.should_stop() is False
    # 走行中は受理し should_stop が立つ。
    state.begin(full_backfill=False)
    assert state.request_stop() is True
    assert state.should_stop() is True


def test_end_returns_to_idle() -> None:
    state.begin(full_backfill=True)
    state.set_current_job("calc_signals")
    state.end()
    s = state.snapshot()
    assert s.running is False
    assert s.current_job is None
    assert s.full_backfill is False
    assert s.stop_requested is False
