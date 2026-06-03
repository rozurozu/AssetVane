"""alembic マイグレーションが fresh DB に通り、想定テーブルを作ることを検証する。

conftest の create_schema 経路とは別に、本番の起動経路（init_db = alembic upgrade head）を
実際に走らせて健全性を担保する（create_all と migration のドリフト検知も兼ねる）。
"""

from __future__ import annotations

from sqlalchemy import inspect, text

from app.config import settings
from app.db.engine import get_engine, init_db, reset_engine


def test_upgrade_head_on_fresh_db(tmp_path, monkeypatch) -> None:
    db_file = tmp_path / "migrate.db"
    monkeypatch.setattr(settings, "database_path", str(db_file))
    reset_engine()

    init_db()  # alembic upgrade head（fresh DB に baseline を適用）

    with get_engine().connect() as conn:
        names = set(inspect(conn).get_table_names())

        # Phase 2（0004/0005）で追加したテーブルが存在することを確認する。
        assert {
            "stocks",
            "daily_quotes",
            "fetch_meta",
            "signals",
            # Phase 2（0004_portfolio_and_assets）
            "portfolios",
            "transactions",
            "holdings",
            "cash",
            "external_assets",
            "index_quotes",
            "asset_snapshots",
            # Phase 2（0005_financials）
            "financials",
            "alembic_version",
        } <= names

        # 0004 マイグレーションで seed した portfolios の初期行（id=1, name='Default'）を確認。
        row = (
            conn.execute(text("SELECT portfolio_id, name FROM portfolios WHERE portfolio_id = 1"))
            .mappings()
            .first()
        )
        assert row is not None, "portfolios に seed 行（id=1）が存在しない"
        assert row["portfolio_id"] == 1
        assert row["name"] == "Default"

    reset_engine()
