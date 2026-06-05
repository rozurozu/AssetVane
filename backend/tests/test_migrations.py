"""alembic マイグレーションが fresh DB に通り、想定テーブルを作ることを検証する。

conftest の create_schema 経路とは別に、本番の起動経路（init_db = alembic upgrade head）を
実際に走らせて健全性を担保する（create_all と migration のドリフト検知も兼ねる）。
"""

from __future__ import annotations

from sqlalchemy import inspect, text

from app.config import settings
from app.db.engine import get_engine, init_db, reset_engine
from app.db.schema import daily_quotes, metadata, stocks


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
            # スクリーニング（0007_screening）
            "valuation_snapshots",
            "screening_filters",
            # Phase 4（0008_dossier・phase4-spec §2）
            "watchlist",
            "stock_dossiers",
            "dossier_sources",
            "alembic_version",
        } <= names

        # watchlist は Phase 4（0008_dossier）に一本化（旧 Phase 2 案からは外した＝§2 注記）。
        # 0004（portfolio）では作られないこと＝二重 CREATE が無いことを保証する回帰。
        wl_cols = {c["name"] for c in inspect(conn).get_columns("watchlist")}
        assert wl_cols == {"id", "code", "note", "added_at"}, (
            "watchlist のカラムが spec §2.1 と一致しない（last_investigated_at は持たない）"
        )

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


def test_upgrade_head_on_existing_phase0_db(tmp_path, monkeypatch) -> None:
    """Phase 0 の既存 DB（create_all 済み・alembic 未 stamp）への upgrade head が非破壊で通る。

    init_db/0001_baseline の docstring が約束する「CREATE は IF NOT EXISTS 相当で非破壊」を
    回帰として固定する。`table.create()` に checkfirst が無いと alembic は 0001 から流して
    既存 stocks と衝突し `table already exists` で起動ごと落ちる（2026-06-04 に実機検出）。
    既存行が温存され、後続 Phase のテーブルまで揃うことも確認する。
    """
    db_file = tmp_path / "phase0.db"
    monkeypatch.setattr(settings, "database_path", str(db_file))
    reset_engine()

    # Phase 0 相当の初期状態を再現: stocks/daily_quotes だけを create_all し、alembic は未 stamp。
    engine = get_engine()
    metadata.create_all(engine, tables=[stocks, daily_quotes])
    with engine.begin() as conn:
        conn.execute(stocks.insert().values(code="7203", company_name="トヨタ自動車"))

    init_db()  # ここで alembic upgrade head。checkfirst が無いと既存 stocks と衝突して落ちる。

    with get_engine().connect() as conn:
        names = set(inspect(conn).get_table_names())
        assert {"stocks", "daily_quotes", "signals", "financials", "alembic_version"} <= names

        # 既存行が温存されている（DROP/再作成されていない＝非破壊）。
        kept = conn.execute(text("SELECT company_name FROM stocks WHERE code = '7203'")).scalar()
        assert kept == "トヨタ自動車", "既存 stocks 行が upgrade で失われた（非破壊でない）"

    reset_engine()
