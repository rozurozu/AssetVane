"""Alembic 環境。

接続 URL とエンジンは app 側（settings / db.engine）から取り、二重管理しない。
online は既存の Engine をそのまま使う（WAL/外部キー/親ディレクトリ作成が効く）。
autogenerate は app.db.schema.metadata を基準にする（スキーマの単一の真実）。
SQLite は ALTER が弱いので render_as_batch=True（バッチ ALTER）を有効にする。
"""

from __future__ import annotations

from alembic import context
from app.config import settings
from app.db.engine import get_engine
from app.db.schema import metadata

target_metadata = metadata


def run_migrations_offline() -> None:
    context.configure(
        url=f"sqlite:///{settings.database_path}",
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = get_engine()
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
