"""ファイルロックの相互排他を固定する（spec §3.5・§8）。

同一ロックパスへ二重に acquire すると BatchAlreadyRunning。解放後は再取得できる。
別 open file description どうしなので、同一プロセス内でも flock は競合する。
"""

from __future__ import annotations

import pytest

from app.batch.lock import BatchAlreadyRunning, acquire


def test_double_acquire_raises(tmp_path) -> None:
    lock_path = str(tmp_path / "batch.lock")
    with acquire(lock_path):
        # ネストした 2 回目の acquire は取れず BatchAlreadyRunning。
        with pytest.raises(BatchAlreadyRunning):
            with acquire(lock_path):
                pass


def test_reacquire_after_release(tmp_path) -> None:
    lock_path = str(tmp_path / "batch.lock")
    with acquire(lock_path):
        pass
    # 解放後は再取得できる（例外が出ない）。
    with acquire(lock_path):
        pass


def test_default_lock_path_used(tmp_path, monkeypatch) -> None:
    # lock_path 省略時は settings.database_path と同じ data/ 配下を使う。
    from app.config import settings

    db_file = tmp_path / "data" / "assetvane.db"
    monkeypatch.setattr(settings, "database_path", str(db_file))
    with acquire():
        # 取得できれば OK（パス解決と mkdir が通る）。
        assert (tmp_path / "data" / "batch.lock").exists()
