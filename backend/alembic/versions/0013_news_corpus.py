"""news: ニュース統合コーパス（旧 general_news ＋ dossier_sources を 1 本に集約・ADR-044）

Revision ID: 0013_news_corpus
Revises: 0012_valuation_metrics
Create Date: 2026-06-07

ADR-044（ニュースを統合コーパスと階層タグに集約する）。旧 2 系統＝銘柄ニュース dossier_sources
（ADR-020・code FK 必須）と一般ニュース general_news（ADR-034・category 列）を 1 本 news に統合し、
記事ごとに level（stock/sector/market/user）・code・sector17_code・category・source の階層タグを
持たせる。本文は保存せず summary と url のみ（ADR-020 堅持）。url UNIQUE で再取得の二重取り込みを
防ぐ。level/code/sector17_code に索引を張り 3 層タグフィルタ取り出し（get_news_context）を速くする。

移行手順: (1) news を create、(2) 旧 general_news の行を level='market' で news へ移し、
(3) 旧 dossier_sources の行を level='stock'（code 引き継ぎ）で news へ移し、url 衝突は無視、
(4) 旧 2 テーブルを drop。FK 参照チェック済み＝他テーブルから dossier_sources/general_news への
ForeignKey 参照は無い（stock_dossiers は stocks.code を参照し dossier_sources は参照しない）ので
drop 前の対処は不要。downgrade は逆操作（2 テーブルを再作成し news から level で振り分けて戻す）。

採番: 直前 head は 0012_valuation_metrics。連鎖を直線に保つため down_revision=0012。冪等性: 既存 DB
への再適用に備えテーブル存在チェックをしてから create/drop する（0011/0012 と同方針）。
DB に触れる OS プロセスは FastAPI 1 つ（ADR-005）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013_news_corpus"
down_revision: str | None = "0012_valuation_metrics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _existing_tables() -> set[str]:
    bind = op.get_bind()
    return set(sa.inspect(bind).get_table_names())


def upgrade() -> None:
    tables = _existing_tables()

    # (a) news を create（統合コーパス本体・ADR-044）。code FK は stock 層のみ・他層は NULL。
    if "news" not in tables:
        op.create_table(
            "news",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("level", sa.String(), nullable=False),  # stock/sector/market/user
            sa.Column("code", sa.String(), nullable=True),  # stock 層の銘柄 FK
            sa.Column("sector17_code", sa.String(), nullable=True),  # sector 層 '1617'..'1633'
            sa.Column("category", sa.String(), nullable=True),  # market 層の表示ラベル
            sa.Column("source", sa.String(), nullable=True),  # 旧 source_type を改名
            sa.Column("url", sa.String(), nullable=False),  # 取り込み元 URL（本文は保存しない）
            sa.Column("title", sa.String(), nullable=True),
            sa.Column("summary", sa.String(), nullable=True),  # 短い要約（全文は捨てる）
            sa.Column("published_at", sa.String(), nullable=True),  # 発行日 'YYYY-MM-DD'
            sa.Column("fetched_at", sa.String(), nullable=True),  # 取り込み時刻 ISO8601 UTC
            sa.Column("extraction_status", sa.String(), nullable=True),  # 取得レベル
            sa.PrimaryKeyConstraint("id", name="pk_news"),
            sa.ForeignKeyConstraint(["code"], ["stocks.code"], name="fk_news_code_stocks"),
            sa.UniqueConstraint("url", name="uq_news_url"),  # URL 重複排除
        )
        op.create_index("ix_news_level", "news", ["level"])
        op.create_index("ix_news_code", "news", ["code"])
        op.create_index("ix_news_sector17", "news", ["sector17_code"])

    # (b) 旧 general_news の行を level='market' で news へ移す（category=元 category・source=元
    # source_type・url 衝突は無視）。INSERT OR IGNORE 相当で url UNIQUE 衝突を握り潰す。
    if "general_news" in tables:
        op.execute(
            sa.text(
                "INSERT OR IGNORE INTO news "
                "(level, code, sector17_code, category, source, url, title, summary, "
                " published_at, fetched_at, extraction_status) "
                "SELECT 'market', NULL, NULL, category, source_type, url, title, summary, "
                " published_at, fetched_at, extraction_status "
                "FROM general_news"
            )
        )

    # (c) 旧 dossier_sources の行を level='stock'（code 引き継ぎ）で news へ移す。旧 processed_at は
    # 取り込み時刻なので news.fetched_at に対応づける。url 衝突は無視（OR IGNORE）。
    if "dossier_sources" in tables:
        op.execute(
            sa.text(
                "INSERT OR IGNORE INTO news "
                "(level, code, sector17_code, category, source, url, title, summary, "
                " published_at, fetched_at, extraction_status) "
                "SELECT 'stock', code, NULL, NULL, source_type, url, title, summary, "
                " published_at, processed_at, extraction_status "
                "FROM dossier_sources"
            )
        )

    # (d) 旧 2 テーブルを drop（完全統合）。索引も併せて消える。
    if "general_news" in tables:
        op.drop_index("ix_general_news_category", table_name="general_news")
        op.drop_table("general_news")
    if "dossier_sources" in tables:
        op.drop_index("ix_dossier_sources_code", table_name="dossier_sources")
        op.drop_table("dossier_sources")


def downgrade() -> None:
    tables = _existing_tables()

    # 逆操作: 旧 2 テーブルを再作成し、news から level で振り分けて戻す。

    # general_news を再作成（ADR-034 の形）。
    if "general_news" not in tables:
        op.create_table(
            "general_news",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("category", sa.String(), nullable=False),
            sa.Column("url", sa.String(), nullable=False),
            sa.Column("title", sa.String(), nullable=True),
            sa.Column("summary", sa.String(), nullable=True),
            sa.Column("published_at", sa.String(), nullable=True),
            sa.Column("fetched_at", sa.String(), nullable=True),
            sa.Column("source_type", sa.String(), nullable=True),
            sa.Column("extraction_status", sa.String(), nullable=True),
            sa.PrimaryKeyConstraint("id", name="pk_general_news"),
            sa.UniqueConstraint("url", name="uq_general_news_url"),
        )
        op.create_index("ix_general_news_category", "general_news", ["category"])

    # dossier_sources を再作成（ADR-020 の形）。
    if "dossier_sources" not in tables:
        op.create_table(
            "dossier_sources",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("code", sa.String(), nullable=False),
            sa.Column("source_type", sa.String(), nullable=True),
            sa.Column("url", sa.String(), nullable=False),
            sa.Column("title", sa.String(), nullable=True),
            sa.Column("summary", sa.String(), nullable=True),
            sa.Column("published_at", sa.String(), nullable=True),
            sa.Column("processed_at", sa.String(), nullable=True),
            sa.Column("extraction_status", sa.String(), nullable=True),
            sa.PrimaryKeyConstraint("id", name="pk_dossier_sources"),
            sa.ForeignKeyConstraint(
                ["code"], ["stocks.code"], name="fk_dossier_sources_code_stocks"
            ),
            sa.UniqueConstraint("url", name="uq_dossier_sources_url"),
        )
        op.create_index("ix_dossier_sources_code", "dossier_sources", ["code"])

    if "news" in tables:
        # market 層 → general_news（source → source_type・category は元のまま）。
        op.execute(
            sa.text(
                "INSERT OR IGNORE INTO general_news "
                "(category, url, title, summary, published_at, fetched_at, source_type, "
                " extraction_status) "
                "SELECT category, url, title, summary, published_at, fetched_at, source, "
                " extraction_status "
                "FROM news WHERE level = 'market'"
            )
        )
        # stock 層 → dossier_sources（fetched_at → processed_at に戻す）。
        op.execute(
            sa.text(
                "INSERT OR IGNORE INTO dossier_sources "
                "(code, source_type, url, title, summary, published_at, processed_at, "
                " extraction_status) "
                "SELECT code, source, url, title, summary, published_at, fetched_at, "
                " extraction_status "
                "FROM news WHERE level = 'stock'"
            )
        )
        # news を drop（sector/user 層は旧 2 テーブルに住所が無く戻せず失われる＝発展置換の宿命）。
        op.drop_index("ix_news_sector17", table_name="news")
        op.drop_index("ix_news_code", table_name="news")
        op.drop_index("ix_news_level", table_name="news")
        op.drop_table("news")
