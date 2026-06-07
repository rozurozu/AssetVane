"""ニュース統合コーパスの repo（upsert_news / news_exists / list_news）を検証する（ADR-044）。

担保すること:
- upsert_news の冪等性（同じ url を 2 回入れても 1 行・既存は skip）と空 rows の 0 返し。
- fetched_at 未指定行は UTC now が補完される。
- news_exists の存在/非存在判定。
- list_news の各フィルタ（level/code/sector17_code/since）と AND 合成・published_at 降順・limit。
旧 general_news/dossier_sources を 1 本に統合したテーブルで、階層タグ（level 等）でフィルタする。
本物の DB に触れず一時 SQLite で回す（stock 層の code FK は NULL も許すので stock 投入は不要・
testing-strategy）。
"""

from __future__ import annotations

from app.db import repo
from app.db.engine import get_engine
from app.db.schema import stocks


def _seed_stocks(*codes: str) -> None:
    """stock 層の news は code FK（stocks.code）を持つため、銘柄を先に入れる（FK 制約充足）。"""
    with get_engine().begin() as conn:
        for code in codes:
            conn.execute(stocks.insert().values(code=code, company_name=f"{code} 社"))


def _row(
    url: str,
    *,
    level: str = "market",
    code: str | None = None,
    sector17_code: str | None = None,
    category: str | None = "市況",
    source: str = "news",
    published_at: str | None = "2026-06-05",
    fetched_at: str | None = None,
) -> dict:
    return {
        "level": level,
        "code": code,
        "sector17_code": sector17_code,
        "category": category,
        "source": source,
        "url": url,
        "title": f"{url} のタイトル",
        "summary": "要約。",
        "published_at": published_at,
        "fetched_at": fetched_at,
        "extraction_status": "summarized",
    }


def test_upsert_news_dedup_by_url(temp_db) -> None:
    """同じ url を 2 回入れても 1 行（on_conflict_do_nothing・冪等）。先勝ちで初回の値が残る。"""
    with get_engine().begin() as conn:
        repo.upsert_news(conn, [_row("https://a.example/1")])
        repo.upsert_news(conn, [_row("https://a.example/1", category="マクロ")])
    with get_engine().connect() as conn:
        rows = repo.list_news(conn)
    assert len(rows) == 1
    assert rows[0]["category"] == "市況"  # 既存 skip = 先勝ち


def test_upsert_news_empty_returns_zero(temp_db) -> None:
    """空 rows は 0 を返し何も入れない。"""
    with get_engine().begin() as conn:
        assert repo.upsert_news(conn, []) == 0
    with get_engine().connect() as conn:
        assert repo.list_news(conn) == []


def test_upsert_news_fills_fetched_at(temp_db) -> None:
    """fetched_at 未指定なら UTC now が補完される。"""
    with get_engine().begin() as conn:
        repo.upsert_news(conn, [_row("https://a.example/2")])
    with get_engine().connect() as conn:
        rows = repo.list_news(conn)
    assert rows[0]["fetched_at"]  # 非 None・非空


def test_news_exists(temp_db) -> None:
    """news_exists は url の存在/非存在を判定する（要約前 dedup 用）。"""
    with get_engine().begin() as conn:
        repo.upsert_news(conn, [_row("https://a.example/exists")])
    with get_engine().connect() as conn:
        assert repo.news_exists(conn, "https://a.example/exists") is True
        assert repo.news_exists(conn, "https://a.example/missing") is False


def test_list_news_filter_by_level(temp_db) -> None:
    """level フィルタで該当層だけ返る。"""
    _seed_stocks("7203")
    with get_engine().begin() as conn:
        repo.upsert_news(
            conn,
            [
                _row("https://a.example/m", level="market"),
                _row("https://a.example/s", level="stock", code="7203", category=None),
            ],
        )
    with get_engine().connect() as conn:
        rows = repo.list_news(conn, level="stock")
    assert [r["url"] for r in rows] == ["https://a.example/s"]
    assert rows[0]["level"] == "stock"


def test_list_news_filter_by_code(temp_db) -> None:
    """code フィルタで該当銘柄だけ返る（stock 層）。"""
    _seed_stocks("7203", "6758")
    with get_engine().begin() as conn:
        repo.upsert_news(
            conn,
            [
                _row("https://a.example/7203", level="stock", code="7203", category=None),
                _row("https://a.example/6758", level="stock", code="6758", category=None),
            ],
        )
    with get_engine().connect() as conn:
        rows = repo.list_news(conn, code="7203")
    assert [r["code"] for r in rows] == ["7203"]


def test_list_news_filter_by_sector17(temp_db) -> None:
    """sector17_code フィルタで該当セクターだけ返る（sector 層）。"""
    with get_engine().begin() as conn:
        repo.upsert_news(
            conn,
            [
                _row(
                    "https://a.example/sec1",
                    level="sector",
                    sector17_code="1617",
                    category=None,
                ),
                _row(
                    "https://a.example/sec2",
                    level="sector",
                    sector17_code="1633",
                    category=None,
                ),
            ],
        )
    with get_engine().connect() as conn:
        rows = repo.list_news(conn, sector17_code="1617")
    assert [r["sector17_code"] for r in rows] == ["1617"]


def test_list_news_filters_are_anded(temp_db) -> None:
    """level と code を併用すると AND で絞り込む。"""
    _seed_stocks("7203")
    with get_engine().begin() as conn:
        repo.upsert_news(
            conn,
            [
                _row("https://a.example/s7203", level="stock", code="7203", category=None),
                _row("https://a.example/u7203", level="user", code="7203", category=None),
            ],
        )
    with get_engine().connect() as conn:
        rows = repo.list_news(conn, level="stock", code="7203")
    assert [r["url"] for r in rows] == ["https://a.example/s7203"]


def test_list_news_since_filter(temp_db) -> None:
    """since 指定で published_at >= since に絞る。"""
    with get_engine().begin() as conn:
        repo.upsert_news(
            conn,
            [
                _row("https://a.example/old", published_at="2026-06-01"),
                _row("https://a.example/new", published_at="2026-06-05"),
            ],
        )
    with get_engine().connect() as conn:
        rows = repo.list_news(conn, since="2026-06-04")
    assert len(rows) == 1
    assert rows[0]["published_at"] == "2026-06-05"


def test_list_news_ordered_desc_and_limit(temp_db) -> None:
    """published_at 降順で返り、limit で件数を絞る。"""
    with get_engine().begin() as conn:
        repo.upsert_news(
            conn,
            [
                _row("https://a.example/d1", published_at="2026-06-01"),
                _row("https://a.example/d3", published_at="2026-06-03"),
                _row("https://a.example/d2", published_at="2026-06-02"),
            ],
        )
    with get_engine().connect() as conn:
        rows = repo.list_news(conn)
        limited = repo.list_news(conn, limit=2)
    assert [r["published_at"] for r in rows] == ["2026-06-03", "2026-06-02", "2026-06-01"]
    assert [r["published_at"] for r in limited] == ["2026-06-03", "2026-06-02"]
