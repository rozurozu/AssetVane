"""テーマタグ repo（themes / stock_themes / company_descriptions）を検証する（ADR-050 改訂/056）。

担保すること:
- upsert_company_description: 新規挿入→テキスト変化時のみ更新（同一テキスト再 UPSERT で
  fetched_at 不変＝「テキスト最終変化時刻」の契約・差分タガーの判定材料）。
- insert_themes_if_absent: on_conflict_do_nothing の冪等（再実行で件数不変・first_seen_at 温存）。
- upsert_stock_themes: 再 UPSERT で行数不変・first_assigned_at 保持・last_seen_at のみ bump。
- prune_stale_stock_themes: cutoff 厳密未満のみ削除（境界一致は残す）・market 限定
  （US を prune しても JP 行は残る＝段階 A の安全弁）。
- list_us_codes_for_theme_tagging: ①未タグ→②説明変化→③古い順ローテの優先順・is_etf 除外・limit。
- find_nearest_theme: vec_distance_cosine 距離昇順・自分以外・embedding NULL 除外
  （sqlite-vec は engine の connect リスナがロード済み＝test_news_embedding と同前提）。
- screen_stocks_by_theme: US/JP 混在 seed で market 絞り・gics_sector/sector17_code 絞り・
  JOIN 補完（company_name/sector ラベル）。
本物の DB に触れず一時 SQLite（temp_db）で回す（testing-strategy）。
"""

from __future__ import annotations

from app.db import repo
from app.db.engine import get_engine
from app.db.schema import fetch_meta, stocks, themes

T1 = "2026-06-01T00:00:00+00:00"
T2 = "2026-06-05T00:00:00+00:00"
T3 = "2026-06-09T12:00:00+00:00"


def _seed_jp_stocks(*rows: tuple[str, str, str]) -> None:
    """JP 銘柄マスタを (code, company_name, sector17_code) で投入する（screen の JOIN 先）。"""
    with get_engine().begin() as conn:
        for code, name, s17 in rows:
            conn.execute(stocks.insert().values(code=code, company_name=name, sector17_code=s17))


def _seed_us_stocks(*rows: tuple[str, str, str | None, int]) -> None:
    """US 銘柄マスタを (symbol, company_name, gics_sector, is_etf) で投入する。"""
    repo.upsert_us_stocks(
        [
            {"symbol": sym, "company_name": name, "gics_sector": gics, "is_etf": is_etf}
            for sym, name, gics, is_etf in rows
        ]
    )


def _seed_theme_meta(code: str, last_fetched: str) -> None:
    """fetch_meta に 'us_themes:<code>' のカーソル（ISO datetime）を投入する。"""
    with get_engine().begin() as conn:
        conn.execute(
            fetch_meta.insert().values(
                source=f"us_themes:{code}",
                last_fetched_date=last_fetched,
                updated_at=last_fetched,
                last_attempt_ok=1,
            )
        )


def _desc_row(code: str, text_: str, fetched_at: str | None = None) -> dict:
    row = {
        "market": "US",
        "code": code,
        "source": "yfinance",
        "description_text": text_,
        "disclosed_date": None,
        "doc_id": None,
    }
    if fetched_at is not None:
        row["fetched_at"] = fetched_at
    return row


# ===== company_descriptions =====


def test_upsert_company_description_insert_and_text_change(temp_db) -> None:
    """新規挿入→テキスト変化で更新（fetched_at も前進）。返り値は影響行数。"""
    assert repo.upsert_company_description(_desc_row("AAPL", "iPhone を作る。", T1)) == 1
    with get_engine().connect() as conn:
        row = repo.get_company_description(conn, "US", "AAPL")
    assert row is not None
    assert row["description_text"] == "iPhone を作る。"
    assert row["fetched_at"] == T1

    # テキストが変わったら更新され fetched_at が前進する。
    assert repo.upsert_company_description(_desc_row("AAPL", "iPhone と Mac を作る。", T3)) == 1
    with get_engine().connect() as conn:
        row = repo.get_company_description(conn, "US", "AAPL")
    assert row is not None
    assert row["description_text"] == "iPhone と Mac を作る。"
    assert row["fetched_at"] == T3


def test_upsert_company_description_same_text_keeps_fetched_at(temp_db) -> None:
    """同一テキストの再 UPSERT は何も更新しない（fetched_at 不変＝テキスト最終変化時刻の契約）。"""
    repo.upsert_company_description(_desc_row("AAPL", "iPhone を作る。", T1))
    # 同一テキスト・新しい fetched_at で再 UPSERT → 据え置き（影響 0 行）。
    assert repo.upsert_company_description(_desc_row("AAPL", "iPhone を作る。", T3)) == 0
    with get_engine().connect() as conn:
        row = repo.get_company_description(conn, "US", "AAPL")
    assert row is not None
    assert row["fetched_at"] == T1  # 変化していないので据え置き


def test_upsert_company_description_updates_from_null_text(temp_db) -> None:
    """既存 description_text が NULL でも新テキストで更新できる（NULL 安全＝IS NOT 判定）。"""
    repo.upsert_company_description(
        {
            "market": "US",
            "code": "AAPL",
            "source": "yfinance",
            "description_text": None,
            "fetched_at": T1,
        }
    )
    assert repo.upsert_company_description(_desc_row("AAPL", "iPhone を作る。", T2)) == 1
    with get_engine().connect() as conn:
        row = repo.get_company_description(conn, "US", "AAPL")
    assert row is not None
    assert row["description_text"] == "iPhone を作る。"
    assert row["fetched_at"] == T2


def test_get_company_description_missing_returns_none(temp_db) -> None:
    """未保存の (market, code) は None。market が違えば別行扱い。"""
    repo.upsert_company_description(_desc_row("AAPL", "テキスト", T1))
    with get_engine().connect() as conn:
        assert repo.get_company_description(conn, "JP", "AAPL") is None
        assert repo.get_company_description(conn, "US", "MSFT") is None


# ===== themes 目録 =====


def test_insert_themes_if_absent_idempotent(temp_db) -> None:
    """on_conflict_do_nothing の冪等（再実行で 0 件・first_seen_at 温存・名前昇順一覧）。"""
    assert repo.insert_themes_if_absent(["防衛", "AI需要", "AI需要"], T1) == 2  # 入力内重複は畳む
    assert repo.insert_themes_if_absent(["AI需要", "防衛"], T3) == 0  # 再実行は全既存
    assert repo.insert_themes_if_absent(["円安メリット"], T2) == 1  # 新出だけ追加
    assert repo.insert_themes_if_absent([], T1) == 0  # 空は 0

    with get_engine().connect() as conn:
        names = repo.list_theme_names(conn)
        counts = {r["name"]: r for r in repo.list_themes_with_counts(conn)}
    assert names == sorted(["AI需要", "防衛", "円安メリット"])
    # first_seen_at は初回投入時のまま（再実行の T3 で潰れていない）。
    assert counts["AI需要"]["first_seen_at"] == T1
    assert counts["円安メリット"]["first_seen_at"] == T2


def test_list_themes_with_counts(temp_db) -> None:
    """LEFT JOIN + GROUP BY で所属銘柄数を数える（未付与テーマは 0）。"""
    repo.insert_themes_if_absent(["AI需要", "防衛"], T1)
    repo.upsert_stock_themes(
        [
            {
                "market": "US",
                "code": "AAPL",
                "theme_name": "AI需要",
                "first_assigned_at": T1,
                "last_seen_at": T1,
            },
            {
                "market": "JP",
                "code": "7203",
                "theme_name": "AI需要",
                "first_assigned_at": T1,
                "last_seen_at": T1,
            },
        ]
    )
    with get_engine().connect() as conn:
        rows = {r["name"]: r for r in repo.list_themes_with_counts(conn)}
    assert rows["AI需要"]["n_stocks"] == 2
    assert rows["防衛"]["n_stocks"] == 0
    assert rows["AI需要"]["near_duplicate_of"] is None


def test_list_themes_needing_embedding_and_update(temp_db) -> None:
    """embedding NULL／モデル不一致の行だけ返り、update_theme_embedding で消える（news 同型）。"""
    repo.insert_themes_if_absent(["AI需要", "防衛", "半導体"], T1)
    repo.update_theme_embedding("防衛", repo.pack_embedding([1.0, 0.0]), "model-A")
    repo.update_theme_embedding("半導体", repo.pack_embedding([0.0, 1.0]), "model-OLD")

    with get_engine().connect() as conn:
        need = repo.list_themes_needing_embedding(conn, current_model="model-A", limit=10)
    # AI需要（未埋め込み）と 半導体（モデル不一致）が対象。防衛（model-A 済み）は対象外。
    assert [r["name"] for r in need] == ["AI需要", "半導体"]

    with get_engine().connect() as conn:
        limited = repo.list_themes_needing_embedding(conn, current_model="model-A", limit=1)
    assert len(limited) == 1


def test_set_theme_near_duplicate(temp_db) -> None:
    """near_duplicate_of の設定/解除（自動マージはしない＝フラグのみ）。"""
    repo.insert_themes_if_absent(["AI需要", "AI関連"], T1)
    repo.set_theme_near_duplicate("AI関連", "AI需要")
    with get_engine().connect() as conn:
        rows = {r["name"]: r for r in repo.list_themes_with_counts(conn)}
    assert rows["AI関連"]["near_duplicate_of"] == "AI需要"

    repo.set_theme_near_duplicate("AI関連", None)
    with get_engine().connect() as conn:
        rows = {r["name"]: r for r in repo.list_themes_with_counts(conn)}
    assert rows["AI関連"]["near_duplicate_of"] is None


def test_find_nearest_theme(temp_db) -> None:
    """距離昇順 LIMIT 1・自分自身は除外・embedding NULL は対象外（vec_distance_cosine 実関数）。"""
    repo.insert_themes_if_absent(["AI需要", "AI関連", "防衛", "未埋込"], T1)
    repo.update_theme_embedding("AI需要", repo.pack_embedding([1.0, 0.0, 0.0]), "m")
    repo.update_theme_embedding("AI関連", repo.pack_embedding([0.9, 0.1, 0.0]), "m")
    repo.update_theme_embedding("防衛", repo.pack_embedding([0.0, 0.0, 1.0]), "m")
    # 「未埋込」は embedding NULL のまま → 候補に出ない。

    qblob = repo.pack_embedding([1.0, 0.0, 0.0])  # AI需要 と同一方向
    with get_engine().connect() as conn:
        nearest = repo.find_nearest_theme(conn, "AI需要", qblob)
    assert nearest is not None
    assert nearest["name"] == "AI関連"  # 自分（AI需要・距離 0）は除外し次点が返る
    assert 0.0 <= nearest["distance"] < 0.1  # ほぼ同方向＝余弦距離は小さい

    # 他に embedding を持つテーマが無ければ None（直接 NULL に戻して確認）。
    with get_engine().begin() as conn:
        conn.execute(
            themes.update()
            .where(themes.c.name.in_(["AI関連", "防衛"]))
            .values(embedding=None, embed_model=None)
        )
    with get_engine().connect() as conn:
        assert repo.find_nearest_theme(conn, "AI需要", qblob) is None


# ===== stock_themes 台帳 =====


def test_upsert_stock_themes_bump_keeps_first_assigned(temp_db) -> None:
    """再 UPSERT で行数不変・first_assigned_at 保持・last_seen_at のみ bump（ADR-050 の要）。"""
    row = {
        "market": "US",
        "code": "AAPL",
        "theme_name": "AI需要",
        "first_assigned_at": T1,
        "last_seen_at": T1,
    }
    assert repo.upsert_stock_themes([row]) == 1

    # 別書き手が同タグを再確認（first_assigned_at は新しい値を渡しても保持される）。
    bumped = dict(row, first_assigned_at=T3, last_seen_at=T3)
    assert repo.upsert_stock_themes([bumped]) == 1

    with get_engine().connect() as conn:
        rows = repo.get_stock_themes(conn, "US", "AAPL")
    assert len(rows) == 1  # 行は増えない（UNIQUE(market,code,theme_name)）
    assert rows[0]["first_assigned_at"] == T1  # 初付与日時は保持
    assert rows[0]["last_seen_at"] == T3  # 最終再確認のみ bump

    assert repo.upsert_stock_themes([]) == 0  # 空は 0


def test_prune_stale_stock_themes_boundary_and_market_scope(temp_db) -> None:
    """cutoff 厳密未満のみ削除（境界一致は残す）・market 限定で JP 行は残る（安全弁）。"""
    repo.upsert_stock_themes(
        [
            {
                "market": "US",
                "code": "OLD",
                "theme_name": "AI需要",
                "first_assigned_at": T1,
                "last_seen_at": T1,
            },
            {
                "market": "US",
                "code": "EDGE",
                "theme_name": "AI需要",
                "first_assigned_at": T2,
                "last_seen_at": T2,
            },  # cutoff ちょうど
            {
                "market": "US",
                "code": "NEW",
                "theme_name": "AI需要",
                "first_assigned_at": T3,
                "last_seen_at": T3,
            },
            {
                "market": "JP",
                "code": "7203",
                "theme_name": "AI需要",
                "first_assigned_at": T1,
                "last_seen_at": T1,
            },  # 古いが JP＝対象外
        ]
    )
    deleted = repo.prune_stale_stock_themes(market="US", cutoff_iso=T2)
    assert deleted == 1  # OLD だけ枯れる

    with get_engine().connect() as conn:
        remaining = {(r["market"], r["code"]) for r in repo.screen_stocks_by_theme(conn, "AI需要")}
    assert remaining == {("US", "EDGE"), ("US", "NEW"), ("JP", "7203")}


# ===== screen_stocks_by_theme =====


def test_screen_stocks_by_theme_mixed_markets_and_filters(temp_db) -> None:
    """US/JP 混在 seed で market 絞り・gics_sector/sector17_code 絞り・JOIN 補完が効く。"""
    _seed_jp_stocks(("7203", "トヨタ自動車", "6"), ("6758", "ソニーグループ", "9"))
    _seed_us_stocks(("AAPL", "Apple Inc.", "Technology", 0), ("XOM", "Exxon Mobil", "Energy", 0))
    repo.insert_themes_if_absent(["AI需要"], T1)
    repo.upsert_stock_themes(
        [
            {
                "market": "JP",
                "code": "7203",
                "theme_name": "AI需要",
                "first_assigned_at": T1,
                "last_seen_at": T1,
            },
            {
                "market": "JP",
                "code": "6758",
                "theme_name": "AI需要",
                "first_assigned_at": T1,
                "last_seen_at": T1,
            },
            {
                "market": "US",
                "code": "AAPL",
                "theme_name": "AI需要",
                "first_assigned_at": T1,
                "last_seen_at": T1,
            },
            {
                "market": "US",
                "code": "XOM",
                "theme_name": "AI需要",
                "first_assigned_at": T1,
                "last_seen_at": T1,
            },
        ]
    )

    with get_engine().connect() as conn:
        all_rows = repo.screen_stocks_by_theme(conn, "AI需要")
        us_rows = repo.screen_stocks_by_theme(conn, "AI需要", market="US")
        tech_rows = repo.screen_stocks_by_theme(conn, "AI需要", gics_sector="Technology")
        jp_auto = repo.screen_stocks_by_theme(conn, "AI需要", sector17_code="6")
        limited = repo.screen_stocks_by_theme(conn, "AI需要", limit=2)
        none_rows = repo.screen_stocks_by_theme(conn, "防衛")

    # market→code 昇順で安定。JOIN 補完（company_name/sector ラベル）が乗る。
    assert [(r["market"], r["code"]) for r in all_rows] == [
        ("JP", "6758"),
        ("JP", "7203"),
        ("US", "AAPL"),
        ("US", "XOM"),
    ]
    by_code = {r["code"]: r for r in all_rows}
    assert by_code["AAPL"]["company_name"] == "Apple Inc."
    assert by_code["AAPL"]["sector"] == "Technology"  # US は gics_sector ラベル
    assert by_code["7203"]["company_name"] == "トヨタ自動車"
    assert by_code["7203"]["sector"] == "6"  # JP は sector17_code
    # バリュエーション数値は含めない（ADR-014）。
    assert "per" not in by_code["AAPL"] and "market_cap" not in by_code["AAPL"]

    assert [r["code"] for r in us_rows] == ["AAPL", "XOM"]
    assert [r["code"] for r in tech_rows] == ["AAPL"]
    assert [r["code"] for r in jp_auto] == ["7203"]
    assert len(limited) == 2
    assert none_rows == []


# ===== list_us_codes_for_theme_tagging =====


def test_list_us_codes_for_theme_tagging_priority_etf_and_limit(temp_db) -> None:
    """①未タグ→②説明変化→③古い順ローテの優先順・is_etf=1 除外・limit が効く（ADR-050/033）。"""
    _seed_us_stocks(
        ("AAA", "A Corp", "Technology", 0),
        ("BBB", "B Corp", "Energy", 0),
        ("CCC", "C Corp", "Utilities", 0),
        ("DDD", "D Corp", "Financials", 0),
        ("ETF1", "Some ETF", None, 1),  # ETF は除外される
    )
    # 説明テキスト（fetched_at はテキスト最終変化時刻・ISO datetime）。
    repo.upsert_company_description(_desc_row("AAA", "A の事業。", T1))  # メタ無し → ①
    repo.upsert_company_description(_desc_row("BBB", "B の事業（更新）。", T3))  # 変化 → ②
    repo.upsert_company_description(_desc_row("CCC", "C の事業。", T1))  # ③（メタ新しめ）
    repo.upsert_company_description(_desc_row("DDD", "D の事業。", T1))  # ③（メタ古い）
    repo.upsert_company_description(_desc_row("ETF1", "ファンドの説明。", T1))  # ETF → 除外

    _seed_theme_meta("BBB", T2)  # T3 > T2 → 説明変化
    _seed_theme_meta("CCC", "2026-06-04T00:00:00+00:00")
    _seed_theme_meta("DDD", "2026-06-02T00:00:00+00:00")  # CCC より古い → 先

    with get_engine().connect() as conn:
        order = repo.list_us_codes_for_theme_tagging(conn, limit=10)
        top2 = repo.list_us_codes_for_theme_tagging(conn, limit=2)
        empty = repo.list_us_codes_for_theme_tagging(conn, limit=0)

    assert order == ["AAA", "BBB", "DDD", "CCC"]  # ①→②→③古い順。ETF1 は出ない
    assert top2 == ["AAA", "BBB"]  # limit で先頭から切る
    assert empty == []


def test_list_us_codes_for_theme_tagging_requires_description(temp_db) -> None:
    """company_descriptions が無い銘柄は対象外（説明テキスト起点＝grounded の前提）。"""
    _seed_us_stocks(("AAA", "A Corp", "Technology", 0), ("BBB", "B Corp", "Energy", 0))
    repo.upsert_company_description(_desc_row("AAA", "A の事業。", T1))
    # JP の説明は market='US' 起点に乗らない。
    repo.upsert_company_description(
        {
            "market": "JP",
            "code": "7203",
            "source": "edinet",
            "description_text": "自動車の製造。",
            "fetched_at": T1,
        }
    )

    with get_engine().connect() as conn:
        order = repo.list_us_codes_for_theme_tagging(conn, limit=10)
    assert order == ["AAA"]  # BBB（説明なし）と 7203（JP）は出ない


# ===== list_jp_codes_for_theme_tagging（段階B＝JP 調査済みドシエ） =====


def _jp_desc_row(code: str, text_: str, fetched_at: str | None = None) -> dict:
    """JP（source='dossier'）の company_descriptions 行（段階B 信号源・investigate 由来）。"""
    row = {
        "market": "JP",
        "code": code,
        "source": "dossier",
        "description_text": text_,
        "disclosed_date": None,
        "doc_id": None,
    }
    if fetched_at is not None:
        row["fetched_at"] = fetched_at
    return row


def _seed_jp_theme_meta(code: str, last_fetched: str) -> None:
    """fetch_meta に 'jp_themes:<code>' のカーソル（ISO datetime）を投入する。"""
    with get_engine().begin() as conn:
        conn.execute(
            fetch_meta.insert().values(
                source=f"jp_themes:{code}",
                last_fetched_date=last_fetched,
                updated_at=last_fetched,
                last_attempt_ok=1,
            )
        )


def _seed_jp_stocks_with_etf(*rows: tuple[str, str, int]) -> None:
    """JP 銘柄マスタを (code, company_name, is_etf) で投入する（is_etf 除外検証用）。"""
    with get_engine().begin() as conn:
        for code, name, etf in rows:
            conn.execute(stocks.insert().values(code=code, company_name=name, is_etf=etf))


def test_list_jp_codes_for_theme_tagging_priority_etf_and_limit(temp_db) -> None:
    """JP も ①未タグ→②変化→③古い順ローテ・is_etf 除外・limit が効く（ADR-050 段階B）。"""
    _seed_jp_stocks_with_etf(
        ("11110", "A社", 0),
        ("22220", "B社", 0),
        ("33330", "C社", 0),
        ("44440", "D社", 0),
        ("99990", "上場ETF", 1),  # ETF は除外される
    )
    repo.upsert_company_description(_jp_desc_row("11110", "A の事業。", T1))  # メタ無し → ①
    repo.upsert_company_description(_jp_desc_row("22220", "B の事業（更新）。", T3))  # 変化 → ②
    repo.upsert_company_description(_jp_desc_row("33330", "C の事業。", T1))  # ③（メタ新しめ）
    repo.upsert_company_description(_jp_desc_row("44440", "D の事業。", T1))  # ③（メタ古い）
    repo.upsert_company_description(_jp_desc_row("99990", "ファンドの説明。", T1))  # ETF → 除外

    _seed_jp_theme_meta("22220", T2)  # T3 > T2 → 説明変化
    _seed_jp_theme_meta("33330", "2026-06-04T00:00:00+00:00")
    _seed_jp_theme_meta("44440", "2026-06-02T00:00:00+00:00")  # 33330 より古い → 先

    with get_engine().connect() as conn:
        order = repo.list_jp_codes_for_theme_tagging(conn, limit=10)
        top2 = repo.list_jp_codes_for_theme_tagging(conn, limit=2)
        empty = repo.list_jp_codes_for_theme_tagging(conn, limit=0)

    assert order == ["11110", "22220", "44440", "33330"]  # ①→②→③古い順。99990 は出ない
    assert top2 == ["11110", "22220"]  # limit で先頭から切る
    assert empty == []


def test_list_jp_codes_for_theme_tagging_excludes_us_rows(temp_db) -> None:
    """market='US' の説明は JP 起点に乗らない（段階A/B の信号源分離・US 版の対称）。"""
    _seed_jp_stocks_with_etf(("11110", "A社", 0))
    repo.upsert_company_description(_jp_desc_row("11110", "A の事業。", T1))
    repo.upsert_company_description(_desc_row("AAPL", "US の事業。", T1))  # market='US'

    with get_engine().connect() as conn:
        order = repo.list_jp_codes_for_theme_tagging(conn, limit=10)
    assert order == ["11110"]  # AAPL（US）は出ない


def test_bump_stock_themes_last_seen(temp_db) -> None:
    """bump_stock_themes_last_seen が指定銘柄の既存タグの last_seen_at だけ更新する（段階B）。"""
    repo.insert_themes_if_absent(["AI需要", "半導体"], T1)
    repo.upsert_stock_themes(
        [
            {
                "market": "JP",
                "code": "11110",
                "theme_name": "AI需要",
                "first_assigned_at": T1,
                "last_seen_at": T1,
            },
            {
                "market": "JP",
                "code": "11110",
                "theme_name": "半導体",
                "first_assigned_at": T1,
                "last_seen_at": T1,
            },
            {
                "market": "JP",
                "code": "22220",
                "theme_name": "AI需要",
                "first_assigned_at": T1,
                "last_seen_at": T1,
            },
        ]
    )

    n = repo.bump_stock_themes_last_seen(market="JP", code="11110", last_seen_at=T3)
    assert n == 2  # 11110 の 2 タグだけ更新

    with get_engine().connect() as conn:
        rows_11110 = repo.get_stock_themes(conn, "JP", "11110")
        rows_22220 = repo.get_stock_themes(conn, "JP", "22220")
    assert all(r["last_seen_at"] == T3 for r in rows_11110)  # bump 済み
    assert all(r["first_assigned_at"] == T1 for r in rows_11110)  # first は不変
    assert rows_22220[0]["last_seen_at"] == T1  # 別銘柄は触らない
