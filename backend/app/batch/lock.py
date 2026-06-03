"""夜間バッチ相互排他のファイルロック（spec §3.5・裁定決定5・B-9）。

夜間バッチ（APScheduler 同居）と、別 OS プロセスで起動されうる手動バッチ
（`python -m app.scripts.backfill --nightly`）の相互排他を `fcntl.flock` で取る。
取れなければ `BatchAlreadyRunning`（`/batch/run` は 409・cron はログのみスキップ）。
標準ライブラリのみ（追加依存なし）。
"""

from __future__ import annotations

import fcntl
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.config import settings


class BatchAlreadyRunning(RuntimeError):
    """既にバッチが実行中でロックを取得できなかった（flock 競合）。"""


def _default_lock_path() -> str:
    """既定のロックファイルパス（DB と同じ data/ 配下に置く）。"""
    db_dir = Path(settings.database_path).resolve().parent
    return str(db_dir / "batch.lock")


@contextmanager
def acquire(lock_path: str | None = None) -> Iterator[None]:
    """`fcntl.flock(LOCK_EX | LOCK_NB)` で排他ロックを取る（spec §3.5）。

    取れなければ `BatchAlreadyRunning` を送出する。ブロック内を抜けると flock は解放され、
    ロックファイル自体は残す（毎回作り直さない・存在しても無害）。
    """
    path = lock_path or _default_lock_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    # ロック保持のためにファイルディスクリプタを開いたまま保つ。
    fd = open(path, "w")  # noqa: SIM115 — flock 保持のため with では閉じない
    try:
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise BatchAlreadyRunning(
                f"バッチが既に実行中です（ロック取得失敗: {path}）。"
            ) from exc
        try:
            yield
        finally:
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
    finally:
        fd.close()
