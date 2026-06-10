"""テーマタグ AI Tool 3 本（list_themes / get_stock_themes / screen_by_theme）を検証する
（ADR-050 改訂・段階A・ADR-014/053）。

担保すること:
- handle_list_themes: 件数・n_stocks 降順（同数は name 昇順）・near_duplicate_of フラグ・limit。
- handle_get_stock_themes: タグ有り（found=True）／未タグ（found=False・error にしない）の両系。
- handle_screen_by_theme: market 絞り・gics_sector 絞り（正式 GICS 名の表記揺れを
  normalize_gics_sector で吸収）・sector17_code 絞り（JP 行・段階 B/C の前方互換）・
  items にバリュエーション数値キーが無いこと（ADR-014: テーマ所属の事実のみ）。
- openai_tools(7) に 3 Tool が露出し、openai_tools(4) には露出しない（min_phase=7 ゲート）。
本物の DB に触れず一時 SQLite（temp_db）で回す（testing-strategy）。async handler は
asyncio.run で駆動する（test_advisor_tools と同流儀）。
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.advisor.tools import handlers
from app.advisor.tools.registry import openai_tools
from app.db import repo
from app.db.engine import get_engine
from app.db.schema import stocks

T1 = "2026-06-01T00:00:00+00:00"
T2 = "2026-06-05T00:00:00+00:00"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _seed_jp_stocks(*rows: tuple[str, str, str]) -> None:
    """JP 銘柄マスタを (code, company_name, sector17_code) で投入する（screen の JOIN 先）。"""
    with get_engine().begin() as conn:
        for code, name, s17 in rows:
            conn.execute(stocks.insert().values(code=code, company_name=name, sector17_code=s17))


def _seed_us_stocks(*rows: tuple[str, str, str | None]) -> None:
    """US 銘柄マスタを (symbol, company_name, gics_sector) で投入する（screen の JOIN 先）。"""
    repo.upsert_us_stocks(
        [
            {"symbol": sym, "company_name": name, "gics_sector": gics, "is_etf": 0}
            for sym, name, gics in rows
        ]
    )


def _seed_tags(*rows: tuple[str, str, str]) -> None:
    """stock_themes を (market, code, theme_name) で投入する（時刻は T1/T2 固定）。"""
    repo.upsert_stock_themes(
        [
            {
                "market": market,
                "code": code,
                "theme_name": theme,
                "first_assigned_at": T1,
                "last_seen_at": T2,
            }
            for market, code, theme in rows
        ]
    )


# ---------------------------------------------------------------------------
# list_themes
# ---------------------------------------------------------------------------


def test_handle_list_themes_counts_and_order(temp_db: None) -> None:
    """目録＋所属銘柄数を n_stocks 降順（同数は name 昇順）で返し、near_dup フラグを通す。"""
    repo.insert_themes_if_absent(["AI需要", "防衛", "生成AI"], T1)
    repo.set_theme_near_duplicate("生成AI", "AI需要")  # 重複候補フラグ（自動マージしない）
    _seed_tags(("US", "AAPL", "AI需要"), ("US", "MSFT", "AI需要"), ("US", "LMT", "防衛"))

    out = _run(handlers.handle_list_themes({}))
    assert "error" not in out
    assert out["count"] == 3
    # n_stocks 降順（AI需要=2 → 防衛=1 → 生成AI=0）。
    assert [i["name"] for i in out["items"]] == ["AI需要", "防衛", "生成AI"]
    assert [i["n_stocks"] for i in out["items"]] == [2, 1, 0]
    by_name = {i["name"]: i for i in out["items"]}
    assert by_name["生成AI"]["near_duplicate_of"] == "AI需要"
    assert by_name["AI需要"]["near_duplicate_of"] is None
    assert by_name["AI需要"]["first_seen_at"] == T1


def test_handle_list_themes_limit_takes_top_n(temp_db: None) -> None:
    """limit 指定時は n_stocks 降順の先頭 N 件（discovery で多い順に見える）。"""
    repo.insert_themes_if_absent(["AI需要", "防衛", "円安メリット"], T1)
    _seed_tags(("US", "AAPL", "AI需要"), ("US", "MSFT", "AI需要"), ("US", "LMT", "防衛"))

    out = _run(handlers.handle_list_themes({"limit": 2}))
    assert out["count"] == 2
    assert [i["name"] for i in out["items"]] == ["AI需要", "防衛"]


# ---------------------------------------------------------------------------
# get_stock_themes
# ---------------------------------------------------------------------------


def test_handle_get_stock_themes_found(temp_db: None) -> None:
    """タグ有り銘柄は found=True＋theme_name 昇順の themes を返す。"""
    repo.insert_themes_if_absent(["AI需要", "半導体"], T1)
    _seed_tags(("US", "NVDA", "半導体"), ("US", "NVDA", "AI需要"), ("US", "AAPL", "AI需要"))

    out = _run(handlers.handle_get_stock_themes({"market": "US", "code": "NVDA"}))
    assert "error" not in out
    assert out["market"] == "US"
    assert out["code"] == "NVDA"
    assert out["found"] is True
    assert [t["theme_name"] for t in out["themes"]] == ["AI需要", "半導体"]  # theme_name 昇順
    assert out["themes"][0]["first_assigned_at"] == T1
    assert out["themes"][0]["last_seen_at"] == T2


def test_handle_get_stock_themes_not_found(temp_db: None) -> None:
    """未タグ銘柄は found=False＋空 themes（error にしない＝ループを落とさない）。"""
    out = _run(handlers.handle_get_stock_themes({"market": "JP", "code": "72030"}))
    assert "error" not in out
    assert out["found"] is False
    assert out["themes"] == []


def test_handle_get_stock_themes_invalid_market(temp_db: None) -> None:
    """market が JP/US 以外なら {"error": ...}（境界で弾く・Literal 検証）。"""
    out = _run(handlers.handle_get_stock_themes({"market": "EU", "code": "X"}))
    assert "error" in out


# ---------------------------------------------------------------------------
# screen_by_theme
# ---------------------------------------------------------------------------


def _seed_screen_fixture() -> None:
    """JP＋US 混在のテーマ所属を仕込む（AI需要 = US 2 件＋JP 1 件・防衛 = US 1 件）。"""
    repo.insert_themes_if_absent(["AI需要", "防衛"], T1)
    _seed_us_stocks(
        ("NVDA", "NVIDIA", "Technology"),
        ("MSFT", "Microsoft", "Technology"),
        ("LMT", "Lockheed Martin", "Industrials"),
    )
    _seed_jp_stocks(("65230", "テスト電機", "9"))
    _seed_tags(
        ("US", "NVDA", "AI需要"),
        ("US", "MSFT", "AI需要"),
        ("JP", "65230", "AI需要"),
        ("US", "LMT", "防衛"),
    )


def test_handle_screen_by_theme_market_filter(temp_db: None) -> None:
    """market 絞り＝US 指定で JP 行が落ち、テーマ exact 一致で他テーマも混ざらない。"""
    _seed_screen_fixture()

    out = _run(handlers.handle_screen_by_theme({"theme": "AI需要"}))
    assert out["theme"] == "AI需要"
    assert out["count"] == 3  # 絞りなしは JP＋US 横断
    assert {(i["market"], i["code"]) for i in out["items"]} == {
        ("US", "NVDA"),
        ("US", "MSFT"),
        ("JP", "65230"),
    }

    out_us = _run(handlers.handle_screen_by_theme({"theme": "AI需要", "market": "US"}))
    assert out_us["count"] == 2
    assert {i["code"] for i in out_us["items"]} == {"NVDA", "MSFT"}


def test_handle_screen_by_theme_gics_filter_normalizes_alias(temp_db: None) -> None:
    """gics_sector 絞り＝正式 GICS 名 "Information Technology" も canonical に正規化して効く。"""
    _seed_screen_fixture()

    out = _run(
        handlers.handle_screen_by_theme(
            {"theme": "AI需要", "gics_sector": "Information Technology"}
        )
    )
    assert out["count"] == 2  # Technology の US 2 件（JP 行は gics 不一致で落ちる）
    assert {i["code"] for i in out["items"]} == {"NVDA", "MSFT"}
    assert all(i["sector"] == "Technology" for i in out["items"])


def test_handle_screen_by_theme_sector17_filter(temp_db: None) -> None:
    """sector17_code 絞り（JP の S17・段階 B/C の前方互換）＝JP 行だけ残る。"""
    _seed_screen_fixture()

    out = _run(handlers.handle_screen_by_theme({"theme": "AI需要", "sector17_code": "9"}))
    assert out["count"] == 1
    assert out["items"][0]["market"] == "JP"
    assert out["items"][0]["code"] == "65230"
    assert out["items"][0]["company_name"] == "テスト電機"


def test_handle_screen_by_theme_items_have_no_valuation_numbers(temp_db: None) -> None:
    """items はテーマ所属の事実のみ＝バリュエーション数値キーを含まない（ADR-014）。"""
    _seed_screen_fixture()

    out = _run(handlers.handle_screen_by_theme({"theme": "AI需要"}))
    assert out["count"] > 0
    for item in out["items"]:
        assert set(item) == {"market", "code", "company_name", "sector", "last_seen_at"}
        # 数値指標（per/pbr/roe/market_cap 等）が紛れていないことを明示的に担保する。
        assert {"per", "pbr", "roe", "market_cap", "dividend_yield"}.isdisjoint(item)


def test_handle_screen_by_theme_missing_theme_is_error(temp_db: None) -> None:
    """必須 theme の欠落は {"error": ...}（境界で弾く・ループは落とさない）。"""
    out = _run(handlers.handle_screen_by_theme({}))
    assert "error" in out


# ---------------------------------------------------------------------------
# Phase ゲート（min_phase=7）
# ---------------------------------------------------------------------------


def test_openai_tools_phase7_exposes_theme_tools() -> None:
    """openai_tools(7) にテーマ 3 Tool が露出する（min_phase=7・CURRENT_PHASE=7 で即露出）。"""
    names = {t["function"]["name"] for t in openai_tools(7)}  # type: ignore[index]
    assert {"list_themes", "get_stock_themes", "screen_by_theme"} <= names


def test_openai_tools_phase4_hides_theme_tools() -> None:
    """openai_tools(4) ではテーマ 3 Tool が露出しない（min_phase=7 ゲート）。"""
    names = {t["function"]["name"] for t in openai_tools(4)}  # type: ignore[index]
    assert {"list_themes", "get_stock_themes", "screen_by_theme"}.isdisjoint(names)
