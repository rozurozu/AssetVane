"""SQLite バックアップ（VACUUM INTO）。

デプロイ直前の退避（scripts/deploy.sh）と将来の定期バックアップ（ADR-017）の双方から呼ぶ。
`policy`/`transactions`/`holdings`/`cash`/`advisor_journal` は手入力の一点もので再取得できないため、
新コンテナ起動（= 起動時 alembic upgrade head・ADR-021）の前に必ず取る（architecture.md §7.3）。

    uv run python -m app.scripts.backup                  # タグ無し（時刻ベースのファイル名）
    uv run python -m app.scripts.backup 20260606-120000  # タグ指定（デプロイの IMAGE_TAG）

VACUUM INTO はライブ DB でも安全に単一ファイルへ書き出せる（ADR-017）。出力は DB と同階層の
`backups/` に置き、直近 KEEP 個だけ残して古いものを削除する（SD/SSD の容量・寿命対策）。
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from app.config import settings

# 残すバックアップ世代数（これより古いものはバックアップのたびに prune する）。
KEEP = 10


def backup(tag: str | None = None) -> Path:
    """現在の DB を `backups/<db名>-<tag>.db` へ VACUUM INTO で退避し、古い世代を prune する。

    tag 未指定なら時刻（YYYYMMDD-HHMMSS）をファイル名に使う。戻り値は出力先パス。
    """
    db_path = Path(settings.database_path).resolve()
    if not db_path.exists():
        raise FileNotFoundError(f"DB が見つからない: {db_path}")

    backups_dir = db_path.parent / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)

    label = tag or datetime.now().strftime("%Y%m%d-%H%M%S")
    out = backups_dir / f"{db_path.stem}-{label}.db"

    # VACUUM INTO はライブ DB を安全に単一ファイルへ書き出す（ADR-017）。? バインドで出力先を渡す。
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("VACUUM INTO ?", (str(out),))
    finally:
        conn.close()

    _prune(backups_dir, db_path.stem)
    return out


def _prune(backups_dir: Path, stem: str) -> None:
    """直近 KEEP 個を残して古いバックアップ（更新時刻が古い順）を削除する。"""
    backups = sorted(
        backups_dir.glob(f"{stem}-*.db"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in backups[KEEP:]:
        old.unlink(missing_ok=True)


def main() -> int:
    tag = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        out = backup(tag)
    except (FileNotFoundError, sqlite3.Error) as exc:
        print(f"✖ バックアップ失敗: {exc}", file=sys.stderr)
        return 1
    print(f"✔ バックアップ: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
