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
            # Phase 4（0008_dossier・phase4-spec §2）。dossier_sources は ADR-044（0013）で
            # news へ統合・drop 済み。stock_dossiers は据え置き。
            "watchlist",
            "stock_dossiers",
            # Phase 6（0010_notifications・phase6-spec §2）
            "notifications",
            # ADR-044（0013_news_corpus）。旧 general_news/dossier_sources を統合コーパスへ。
            "news",
            # Phase 7(B-1)（0017_us_equity・ADR-031/039）。米株 3 テーブル（提示専用・別系統）。
            "us_stocks",
            "us_daily_quotes",
            "us_valuation_snapshots",
            "alembic_version",
        } <= names

        # 旧 general_news / dossier_sources は ADR-044（0013）で news へ統合・drop 済み。
        assert "general_news" not in names, "general_news は 0013 で drop 済みのはず"
        assert "dossier_sources" not in names, "dossier_sources は 0013 で drop 済みのはず"

        # news は ADR-044（0013）の統合コーパス。url UNIQUE・level/code/sector17 索引・階層タグ列。
        # ADR-045（0016）で意味検索用の embedding/embed_model/embedded_at 3 列を追加。
        # ADR-049/051（0020）で定性 polarity 列を追加。
        news_cols = {c["name"] for c in inspect(conn).get_columns("news")}
        assert news_cols == {
            "id",
            "level",
            "code",
            "sector17_code",
            "category",
            "source",
            "url",
            "title",
            "summary",
            "published_at",
            "fetched_at",
            "extraction_status",
            # ADR-045（0016_news_embedding）: ニュース意味検索 段階A。
            "embedding",
            "embed_model",
            "embedded_at",
            # ADR-049/051（0020_news_polarity）: 定性センチメント・能動配信の前提。
            "polarity",
        }, "news のカラムが ADR-044/045/049/051 と不一致（0013/0016/0020 が当たっていない）"
        news_uniques = {u["name"] for u in inspect(conn).get_unique_constraints("news")}
        assert "uq_news_url" in news_uniques, "news に url UNIQUE が無い（0013 未適用）"

        # notifications は Phase 6（0010_notifications）で追加。(notify_key, channel) 複合 PK。
        notif_pk = {
            c for c in inspect(conn).get_pk_constraint("notifications")["constrained_columns"]
        }
        assert notif_pk == {"notify_key", "channel"}, (
            "notifications の PK が (notify_key, channel) でない（0010 が当たっていない）"
        )

        # watchlist は Phase 4（0008_dossier）に一本化（旧 Phase 2 案からは外した＝§2 注記）。
        # 0004（portfolio）では作られないこと＝二重 CREATE が無いことを保証する回帰。
        # interval_days は 0009 で追加（銘柄別調査間隔・既定 21＝ADR-033）。
        wl_cols = {c["name"] for c in inspect(conn).get_columns("watchlist")}
        assert wl_cols == {"id", "code", "note", "added_at", "interval_days"}, (
            "watchlist のカラムが ADR-033 と不一致（last_investigated_at は持たない）"
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
