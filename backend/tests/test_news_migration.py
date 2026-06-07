"""0013_news_corpus マイグレーションの回帰テスト（ADR-044）。

担保すること:
- 0012 まで上げた DB（旧 general_news / dossier_sources を持つ）に行を入れ、0013 upgrade で
  それらが統合テーブル news へ level 付き（market / stock）で移ること。
- 旧 2 テーブル（general_news / dossier_sources）が drop されていること。
- news の列・UNIQUE・索引が ADR-044 のとおりであること。

本番の起動経路（alembic upgrade）を実際に走らせて健全性を担保する（test_migrations.py を手本に）。
"""

from __future__ import annotations

from sqlalchemy import inspect, text

from alembic import command
from app.config import settings
from app.db.engine import _alembic_config, get_engine, reset_engine


def test_0013_migrates_old_news_tables_into_unified_news(tmp_path, monkeypatch) -> None:
    db_file = tmp_path / "news_migrate.db"
    monkeypatch.setattr(settings, "database_path", str(db_file))
    reset_engine()

    cfg = _alembic_config()

    # まず 0012 まで上げる（旧 general_news / dossier_sources がある状態）。
    command.upgrade(cfg, "0012_valuation_metrics")

    # 旧 2 テーブルに行を入れる。dossier_sources は code FK 必須なので stocks に銘柄を先に入れる。
    with get_engine().begin() as conn:
        conn.execute(text("INSERT INTO stocks (code, company_name) VALUES ('7203', 'トヨタ')"))
        conn.execute(
            text(
                "INSERT INTO general_news "
                "(category, url, title, summary, published_at, fetched_at, source_type, "
                " extraction_status) "
                "VALUES ('市況', 'https://g.example/1', '市況タイトル', '要約G', "
                " '2026-06-05', '2026-06-05T00:00:00+00:00', 'news', 'summarized')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO dossier_sources "
                "(code, source_type, url, title, summary, published_at, processed_at, "
                " extraction_status) "
                "VALUES ('7203', 'news', 'https://d.example/1', '銘柄タイトル', '要約D', "
                " '2026-06-04', '2026-06-04T00:00:00+00:00', 'summarized')"
            )
        )

    # 0013 を当てる（統合）。
    command.upgrade(cfg, "0013_news_corpus")

    with get_engine().connect() as conn:
        names = set(inspect(conn).get_table_names())
        # 旧 2 テーブルは消え、news が居る。
        assert "news" in names
        assert "general_news" not in names, "general_news が drop されていない"
        assert "dossier_sources" not in names, "dossier_sources が drop されていない"

        # news の列が ADR-044 のとおり。
        cols = {c["name"] for c in inspect(conn).get_columns("news")}
        assert cols == {
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
        }, "news のカラムが ADR-044 と不一致（0013 が当たっていない）"

        uniques = {u["name"] for u in inspect(conn).get_unique_constraints("news")}
        assert "uq_news_url" in uniques, "news に url UNIQUE が無い"
        index_names = {i["name"] for i in inspect(conn).get_indexes("news")}
        assert {"ix_news_level", "ix_news_code", "ix_news_sector17"} <= index_names, (
            "news に level/code/sector17 索引が無い"
        )

        # 旧 general_news 行が level='market' で移る（category/source 引き継ぎ・code は NULL）。
        market = (
            conn.execute(text("SELECT * FROM news WHERE url = 'https://g.example/1'"))
            .mappings()
            .first()
        )
        assert market is not None
        assert market["level"] == "market"
        assert market["category"] == "市況"
        assert market["source"] == "news"  # 旧 source_type → source
        assert market["code"] is None
        assert market["sector17_code"] is None

        # 旧 dossier_sources 行が level='stock' で移る（code 引き継ぎ・processed_at→fetched_at）。
        stock = (
            conn.execute(text("SELECT * FROM news WHERE url = 'https://d.example/1'"))
            .mappings()
            .first()
        )
        assert stock is not None
        assert stock["level"] == "stock"
        assert stock["code"] == "7203"
        assert stock["source"] == "news"
        assert stock["fetched_at"] == "2026-06-04T00:00:00+00:00"  # 旧 processed_at を引き継ぐ
        assert stock["category"] is None
        assert stock["sector17_code"] is None

    reset_engine()
