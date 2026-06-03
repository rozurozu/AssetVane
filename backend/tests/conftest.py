"""pytest 共通フィクスチャ。

各テストは tmp_path の使い捨て SQLite を使い、本物の DB（data/assetvane.db）に触れない。
スキーマは create_schema（metadata 直接）で速く用意する。alembic 経路は test_migrations で別途検証。
ネットには出ない（J-Quants 取得は呼ばない。正規化はサンプル dict で検証）。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.config import settings
from app.db import engine as db_engine


@pytest.fixture
def temp_db(tmp_path, monkeypatch) -> Iterator[None]:
    """settings.database_path を一時ファイルに差し替え、空スキーマを用意する。"""
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(settings, "database_path", str(db_file))
    db_engine.reset_engine()
    db_engine.create_schema()
    yield
    db_engine.reset_engine()


@pytest.fixture
def client(tmp_path, monkeypatch) -> Iterator[object]:
    """TestClient（lifespan 発火 = init_db）。一時 SQLite を alembic 経路で用意する。

    `temp_db`（create_schema）には依存しない。lifespan の `init_db()`（alembic upgrade）が
    スキーマを作るので、create_schema と二重に作ると `op.create_table`（0002/0003）が
    "table already exists" で落ちるため。本番と同じ alembic 経路でスキーマを得る。
    """
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(settings, "database_path", str(db_file))
    db_engine.reset_engine()

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c

    db_engine.reset_engine()
