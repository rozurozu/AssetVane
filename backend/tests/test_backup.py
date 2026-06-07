"""app.scripts.backup のテスト（一時 SQLite・ADR-017）。

VACUUM INTO で有効な SQLite コピーが出来ること、直近 KEEP 個に prune されることを検証する。
ネットには出ない・本物の DB に触れない（testing-strategy・conftest の temp_db を使う）。
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from app.config import settings
from app.scripts.backup import backup


def test_backup_creates_valid_copy(temp_db) -> None:
    """指定タグで backups/<db名>-<tag>.db が出来て、開ける SQLite であること。"""
    out = backup("20260606-120000")

    assert out.exists()
    assert out.name == "test-20260606-120000.db"
    assert out.parent.name == "backups"

    # 出力は正しい SQLite（sqlite_master を読める）。
    conn = sqlite3.connect(str(out))
    try:
        conn.execute("SELECT name FROM sqlite_master").fetchall()
    finally:
        conn.close()


def test_backup_prunes_to_keep(temp_db, monkeypatch) -> None:
    """settings.backup_keep を超える世代があると、古い順に削られて keep 個に揃うこと。"""
    keep = 3
    monkeypatch.setattr(settings, "backup_keep", keep)

    db_path = Path(settings.database_path).resolve()
    backups_dir = db_path.parent / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)

    # keep+2 個の古いダミーを mtime 昇順（old00 が最古）で置く。
    for i in range(keep + 2):
        f = backups_dir / f"{db_path.stem}-old{i:02d}.db"
        f.write_bytes(b"")
        os.utime(f, (1000 + i, 1000 + i))

    # 新規バックアップ（mtime は現在＝最新）で合計 keep+3 → prune 後 keep 個。
    backup("99999999-999999")

    remaining = list(backups_dir.glob(f"{db_path.stem}-*.db"))
    assert len(remaining) == keep
    # 最新（今作ったもの）は残り、一番古いダミーは消える。
    assert (backups_dir / f"{db_path.stem}-99999999-999999.db").exists()
    assert not (backups_dir / f"{db_path.stem}-old00.db").exists()
