"""DB エンジンと接続（SQLAlchemy Core・同期）。

SQLite（WAL モード）。書き手は将来の夜間バッチ 1 つに限定する設計（ADR-002）。
SQLite は同期 I/O なので同期 Engine を使い、FastAPI の同期ルートはスレッドプールで
捌かれる（単一ユーザー規模では async + aiosqlite は過剰）。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, event, text

from alembic import command
from app.config import settings
from app.db.schema import metadata

# backend/ ディレクトリ（alembic/ と alembic.ini の置き場）。engine.py = backend/app/db/engine.py。
_BACKEND_DIR = Path(__file__).resolve().parents[2]

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


def reset_engine() -> None:
    """テスト用: Engine キャッシュを破棄する（settings.database_path を差し替えた後に呼ぶ）。"""
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None


def _alembic_config() -> Config:
    """プログラムから alembic を叩くための設定（script_location を絶対パスで固定）。

    接続 URL/エンジンは alembic/env.py が settings・db.engine から導出する（二重管理しない）。
    """
    cfg = Config()
    cfg.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    return cfg


def init_db() -> None:
    """スキーマを最新化する（`alembic upgrade head`）。

    baseline マイグレーションは `metadata.create_all` 方式なので、既に create_all で作られた
    既存 DB に対して upgrade しても**非破壊**（CREATE は IF NOT EXISTS 相当）で alembic_version を
    付与でき、fresh DB なら全テーブルを作る。スキーマ変更は autogenerate で別リビジョンに刻む。
    """
    command.upgrade(_alembic_config(), "head")


def create_schema() -> None:
    """テスト用: マイグレーションを介さず metadata から直接スキーマを作る（高速・分離）。"""
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
