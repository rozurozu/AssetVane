"""Phase 4 Stock Dossier の repo（watchlist/stock_dossiers/dossier_sources）を検証する。

担保すること（phase4-spec §2/§3・ADR-020/ADR-002）:
- upsert_dossier の冪等性（code conflict で更新・行は増えない）。
- upsert_dossier_source の冪等性（同じ url を 2 回入れても 1 行・既存は skip）。
- dossier_source_exists の存在判定。
- list_watchlist の JOIN（company_name 補完・dossier 無し銘柄は last_investigated_at が None）。
- add_watchlist / remove_watchlist（UNIQUE(code) 衝突は重複として既存行を返す）。
本物の DB に触れず一時 SQLite で回す（ネットに出ない＝testing-strategy）。
"""

from __future__ import annotations

from app.db import repo
from app.db.engine import get_engine

STOCK_A = {
    "code": "7203",
    "company_name": "トヨタ自動車",
    "sector33_code": "3700",
    "sector17_code": "6",
    "market_code": "0111",
    "is_etf": 0,
    "updated_at": "2026-06-05T00:00:00+00:00",
}
STOCK_B = {
    "code": "6758",
    "company_name": "ソニーグループ",
    "sector33_code": "3650",
    "sector17_code": "5",
    "market_code": "0111",
    "is_etf": 0,
    "updated_at": "2026-06-05T00:00:00+00:00",
}


def test_upsert_dossier_is_idempotent(temp_db) -> None:
    """同じ code を 2 回 upsert しても 1 行で、summary_md/last_investigated_at が上書きされる。"""
    repo.upsert_stocks([STOCK_A])
    with get_engine().begin() as conn:
        repo.upsert_dossier(
            conn,
            code="7203",
            summary_md="初回の要約",
            key_facts='{"per": 12.0}',
            last_investigated_at="2026-06-01T02:00:00+00:00",
            updated_at="2026-06-01T02:00:00+00:00",
        )
        repo.upsert_dossier(
            conn,
            code="7203",
            summary_md="更新後の要約",
            key_facts='{"per": 13.5}',
            last_investigated_at="2026-06-05T02:00:00+00:00",
            updated_at="2026-06-05T02:00:00+00:00",
        )
    with get_engine().connect() as conn:
        d = repo.get_dossier(conn, "7203")
    assert d is not None
    assert d["summary_md"] == "更新後の要約"  # living document が上書きされている
    assert d["key_facts"] == '{"per": 13.5}'  # JSON はパースせず生で返る
    assert d["last_investigated_at"] == "2026-06-05T02:00:00+00:00"


def test_get_dossier_missing_returns_none(temp_db) -> None:
    """未調査の銘柄は get_dossier が None を返す。"""
    repo.upsert_stocks([STOCK_A])
    with get_engine().connect() as conn:
        assert repo.get_dossier(conn, "7203") is None


def test_upsert_dossier_source_dedup_by_url(temp_db) -> None:
    """同じ url を 2 回 upsert しても 1 行（既存は skip・本文は保存しない＝ADR-020）。"""
    repo.upsert_stocks([STOCK_A])
    with get_engine().begin() as conn:
        repo.upsert_dossier_source(
            conn,
            code="7203",
            url="https://example.com/a",
            title="記事A",
            summary="要約A",
            published_at="2026-06-03",
            source_type="news",
        )
        # 2 回目（同じ url・別の title/summary）→ skip され元のまま。
        repo.upsert_dossier_source(
            conn,
            code="7203",
            url="https://example.com/a",
            title="記事A改",
            summary="要約A改",
            published_at="2026-06-04",
            source_type="news",
        )
    with get_engine().connect() as conn:
        sources = repo.list_dossier_sources(conn, "7203")
    assert len(sources) == 1  # url UNIQUE で重複しない
    assert sources[0]["title"] == "記事A"  # 既存 skip なので初回の値のまま
    # 本文列が存在しない（summary と url のみ＝ADR-020）。
    assert "body" not in sources[0]
    assert set(sources[0].keys()) == {
        "id",
        "code",
        "source_type",
        "url",
        "title",
        "summary",
        "published_at",
        "processed_at",
    }


def test_dossier_source_exists(temp_db) -> None:
    """dossier_source_exists が既存 url で True・未登録で False を返す。"""
    repo.upsert_stocks([STOCK_A])
    with get_engine().begin() as conn:
        repo.upsert_dossier_source(conn, code="7203", url="https://example.com/x")
    with get_engine().connect() as conn:
        assert repo.dossier_source_exists(conn, "https://example.com/x") is True
        assert repo.dossier_source_exists(conn, "https://example.com/none") is False


def test_list_dossier_sources_ordered_desc(temp_db) -> None:
    """list_dossier_sources が published_at 降順で返す。"""
    repo.upsert_stocks([STOCK_A])
    with get_engine().begin() as conn:
        repo.upsert_dossier_source(conn, code="7203", url="u1", published_at="2026-06-01")
        repo.upsert_dossier_source(conn, code="7203", url="u3", published_at="2026-06-03")
        repo.upsert_dossier_source(conn, code="7203", url="u2", published_at="2026-06-02")
    with get_engine().connect() as conn:
        sources = repo.list_dossier_sources(conn, "7203")
    assert [s["published_at"] for s in sources] == ["2026-06-03", "2026-06-02", "2026-06-01"]


def test_list_watchlist_joins_dossier_and_name(temp_db) -> None:
    """list_watchlist が company_name を JOIN し、dossier 無し銘柄は調査日が None で返る。"""
    repo.upsert_stocks([STOCK_A, STOCK_B])
    repo.add_watchlist("7203", note="主力")
    repo.add_watchlist("6758")
    # 7203 のみ dossier あり（last_investigated_at が付与される）。6758 は dossier 無し。
    with get_engine().begin() as conn:
        repo.upsert_dossier(
            conn,
            code="7203",
            summary_md="x",
            key_facts=None,
            last_investigated_at="2026-06-05T02:00:00+00:00",
            updated_at="2026-06-05T02:00:00+00:00",
        )
    with get_engine().connect() as conn:
        items = repo.list_watchlist(conn)
    by_code = {it["code"]: it for it in items}
    assert by_code["7203"]["company_name"] == "トヨタ自動車"
    assert by_code["7203"]["note"] == "主力"
    assert by_code["7203"]["last_investigated_at"] == "2026-06-05T02:00:00+00:00"
    # dossier 未作成の銘柄は last_investigated_at が None で返る（LEFT JOIN）。
    assert by_code["6758"]["company_name"] == "ソニーグループ"
    assert by_code["6758"]["last_investigated_at"] is None


def test_add_watchlist_duplicate_returns_existing(temp_db) -> None:
    """UNIQUE(code) 衝突時は do_nothing で既存行を返す（重複は増えない＝spec §5.1）。"""
    repo.upsert_stocks([STOCK_A])
    first = repo.add_watchlist("7203", note="初回")
    again = repo.add_watchlist("7203", note="二回目")  # 重複 → 既存行を返す
    assert first["id"] == again["id"]
    assert again["note"] == "初回"  # do_nothing なので note は上書きされない
    with get_engine().connect() as conn:
        items = repo.list_watchlist(conn)
    assert len(items) == 1  # 行は増えていない


def test_remove_watchlist(temp_db) -> None:
    """remove_watchlist が id 行を削除する。"""
    repo.upsert_stocks([STOCK_A])
    row = repo.add_watchlist("7203")
    repo.remove_watchlist(row["id"])
    with get_engine().connect() as conn:
        assert repo.list_watchlist(conn) == []
