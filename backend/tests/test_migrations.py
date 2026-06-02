"""alembic マイグレーションが fresh DB に通り、想定テーブルを作ることを検証する。

conftest の create_schema 経路とは別に、本番の起動経路（init_db = alembic upgrade head）を
実際に走らせて健全性を担保する（create_all と migration のドリフト検知も兼ねる）。
"""

from __future__ import annotations

from sqlalchemy import inspect

from app.config import settings
from app.db.engine import get_engine, init_db, reset_engine


def test_upgrade_head_on_fresh_db(tmp_path, monkeypatch) -> None:
    db_file = tmp_path / "migrate.db"
    monkeypatch.setattr(settings, "database_path", str(db_file))
    reset_engine()

    init_db()  # alembic upgrade head（fresh DB に baseline を適用）

    with get_engine().connect() as conn:
        names = set(inspect(conn).get_table_names())
    reset_engine()
    # 業務テーブル＋ alembic のバージョン管理テーブルが揃う。
    assert {"stocks", "daily_quotes", "alembic_version"} <= names
