"""DB エンジンと接続（SQLAlchemy Core・同期）。

SQLite（WAL モード）。書き手は将来の夜間バッチ 1 つに限定する設計（ADR-002）。
SQLite は同期 I/O なので同期 Engine を使い、FastAPI の同期ルートはスレッドプールで
捌かれる（単一ユーザー規模では async + aiosqlite は過剰）。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import Connection, Engine, create_engine, event, text

from app.config import settings
from app.db.schema import metadata

_engine: Engine | None = None


def _build_engine() -> Engine:
    """設定の database_path から SQLite Engine を作る。親ディレクトリは無ければ作る。"""
    db_path = Path(settings.database_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # check_same_thread=False: FastAPI の同期ルートはスレッドプールで動くため、
    # プールから貸し出した接続が別スレッドで使われても弾かれないようにする。
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    # 接続のたびに WAL と外部キーを有効化する（ADR-002）。
    # journal_mode=WAL は DB ファイルに永続するが、毎回指定しても無害。
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _record):  # noqa: ANN001, ANN202
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return engine


def get_engine() -> Engine:
    """プロセス内で共有する Engine を返す（遅延生成）。"""
    global _engine
    if _engine is None:
        _engine = _build_engine()
    return _engine


def init_db() -> None:
    """スキーマを作成する（冪等＝CREATE TABLE IF NOT EXISTS 相当）。

    Phase 0 は create_all で足りる。スキーマが育って差分管理が要る段になったら
    Alembic を同じ metadata の上に導入する（計画の「移行規律」）。
    """
    metadata.create_all(get_engine())


def get_conn() -> Iterator[Connection]:
    """FastAPI 依存性。読み取り用の接続を貸し出して確実に閉じる。

    書き込み（UPSERT）は repo 側で `with engine.begin()` を使う。
    """
    with get_engine().connect() as conn:
        yield conn


def healthcheck() -> bool:
    """DB に到達できるかの簡易チェック（/health 用）。"""
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
