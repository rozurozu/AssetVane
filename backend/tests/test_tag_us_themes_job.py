"""tag_us_themes ジョブの巡回・カーソル前進・部分失敗・prune 同居を検証する（ADR-050/033/018）。

担保すること:
- 種テーマ（SEED_THEMES）が themes 目録へ冪等に入る（コールドスタート・ADR-050）。
- 選定（list_us_codes_for_theme_tagging）→ タグ付け成功で fetch_meta['us_themes:<code>'] が
  **ISO datetime（時刻まで）**で前進する（差分判定の前提・ADR-050）。
- 1 銘柄の失敗で ok=False になりつつ後続銘柄を止めない（ADR-018）。失敗銘柄は
  last_attempt_ok=0 を記録する。
- 末尾の時間窓 prune が呼ばれ（US の stale タグだけ枯れる・JP は触らない）、detail に
  タグ付け/新テーマ/prune が集約される。
- 選定 0 件（company_descriptions 空）は ok=True・rows=0 で静かに返し prune もしない。

tag_stock_themes は monkeypatch（LLM/ネットに出ない・testing-strategy）。
"""

from __future__ import annotations

from typing import Any

from app.batch.jobs import tag_us_themes
from app.config import settings
from app.db import repo
from app.db.engine import get_engine
from app.reference.theme_seeds import SEED_THEMES


def _seed_us_stock(symbol: str, *, is_etf: int = 0) -> None:
    """選定 JOIN の前提となる us_stocks 行を入れる（is_etf=0 が巡回対象）。"""
    repo.upsert_us_stocks(
        [{"symbol": symbol, "company_name": f"{symbol} Inc", "is_etf": is_etf, "updated_at": "t"}]
    )


def _seed_description(symbol: str, text: str = "designs widgets") -> None:
    """選定の起点となる company_descriptions 行を入れる（market='US'）。"""
    repo.upsert_company_description(
        {
            "market": "US",
            "code": symbol,
            "source": "yfinance",
            "description_text": text,
            "disclosed_date": None,
            "doc_id": None,
            "fetched_at": "2026-06-01T00:00:00+00:00",
        }
    )


def _make_fake_tagger(calls: list[str], *, fail_codes: set[str] | None = None):
    """tag_stock_themes の fake（async・呼び出し記録＋指定銘柄で例外）。"""
    fail = fail_codes or set()

    async def _fake(conn, *, market: str, code: str) -> dict[str, Any]:  # noqa: ANN001 — テスト fake
        calls.append(code)
        if code in fail:
            raise RuntimeError("LLM down")
        return {"code": code, "themes": ["生成AI"], "n_new_themes": 1}

    return _fake


def test_seed_themes_and_cursor_advance(temp_db, monkeypatch) -> None:
    """種テーマが目録に入り、成功銘柄の fetch_meta が ISO datetime で前進する（ADR-050）。"""
    _seed_us_stock("AAA")
    _seed_us_stock("BBB")
    _seed_description("AAA")
    _seed_description("BBB")

    calls: list[str] = []
    monkeypatch.setattr(tag_us_themes, "tag_stock_themes", _make_fake_tagger(calls))
    result = tag_us_themes.run()

    assert result.ok is True
    assert result.rows == 2
    assert sorted(calls) == ["AAA", "BBB"]

    with get_engine().connect() as conn:
        names = repo.list_theme_names(conn)
        meta = repo.get_fetch_meta(conn, "us_themes:AAA")
    # 種テーマが冪等に入っている（コールドスタート語彙）。
    assert set(SEED_THEMES) <= set(names)
    # カーソルは ISO datetime（時刻まで）＝「説明変化」判定の文字列比較が成立する形式。
    assert meta is not None
    assert "T" in str(meta["last_fetched_date"])
    # detail にタグ付け/新テーマ/prune が集約される。
    assert "新テーマ 2 件" in result.detail
    assert "prune 0 行" in result.detail


def test_nightly_cap_limits_targets(temp_db, monkeypatch) -> None:
    """夜あたり天井（theme_tagging_nightly_max）で処理本数が頭打ちになる（ADR-033）。"""
    for symbol in ("AAA", "BBB", "CCC"):
        _seed_us_stock(symbol)
        _seed_description(symbol)
    monkeypatch.setattr(settings, "theme_tagging_nightly_max", 2)

    calls: list[str] = []
    monkeypatch.setattr(tag_us_themes, "tag_stock_themes", _make_fake_tagger(calls))
    result = tag_us_themes.run()

    assert result.ok is True
    assert result.rows == 2
    assert len(calls) == 2


def test_partial_failure_keeps_going(temp_db, monkeypatch) -> None:
    """1 銘柄の失敗で ok=False になりつつ後続を止めない（ADR-018）。失敗は last_attempt_ok=0。"""
    for symbol in ("AAA", "BAD", "CCC"):
        _seed_us_stock(symbol)
        _seed_description(symbol)

    calls: list[str] = []
    monkeypatch.setattr(
        tag_us_themes, "tag_stock_themes", _make_fake_tagger(calls, fail_codes={"BAD"})
    )
    result = tag_us_themes.run()

    assert result.ok is False  # 失敗 1 件あり
    assert result.rows == 2  # 成功は 2 銘柄
    assert sorted(calls) == ["AAA", "BAD", "CCC"]  # 後続を止めていない
    assert "BAD" in result.detail

    with get_engine().connect() as conn:
        bad_meta = repo.get_fetch_meta(conn, "us_themes:BAD")
        ok_meta = repo.get_fetch_meta(conn, "us_themes:AAA")
    assert bad_meta is not None and bad_meta["last_attempt_ok"] == 0  # 失敗を記録
    assert bad_meta["last_fetched_date"] is None  # 再開点は潰さない（未取得のまま）
    assert ok_meta is not None and ok_meta["last_fetched_date"]  # 成功銘柄は前進


def test_prune_runs_with_tagger_and_detail(temp_db, monkeypatch) -> None:
    """末尾 prune が US の stale タグだけ枯らし JP は触らない（ADR-050・market 安全弁）。"""
    _seed_us_stock("AAA")
    _seed_description("AAA")
    # stale な US タグ（90 日より古い）・新鮮な US タグ・stale な JP タグを仕込む。
    repo.insert_themes_if_absent(["旧テーマ", "新テーマ"], "2020-01-01T00:00:00+00:00")
    repo.upsert_stock_themes(
        [
            {
                "market": "US",
                "code": "ZZZ",
                "theme_name": "旧テーマ",
                "first_assigned_at": "2020-01-01T00:00:00+00:00",
                "last_seen_at": "2020-01-01T00:00:00+00:00",
            },
            {
                "market": "US",
                "code": "AAA",
                "theme_name": "新テーマ",
                "first_assigned_at": "2099-01-01T00:00:00+00:00",
                "last_seen_at": "2099-01-01T00:00:00+00:00",
            },
            {
                "market": "JP",
                "code": "72030",
                "theme_name": "旧テーマ",
                "first_assigned_at": "2020-01-01T00:00:00+00:00",
                "last_seen_at": "2020-01-01T00:00:00+00:00",
            },
        ]
    )

    calls: list[str] = []
    monkeypatch.setattr(tag_us_themes, "tag_stock_themes", _make_fake_tagger(calls))
    result = tag_us_themes.run()

    assert result.ok is True
    assert "prune 1 行" in result.detail  # stale な US タグ 1 行だけ枯れた

    with get_engine().connect() as conn:
        us_stale = repo.get_stock_themes(conn, "US", "ZZZ")
        us_fresh = repo.get_stock_themes(conn, "US", "AAA")
        jp_stale = repo.get_stock_themes(conn, "JP", "72030")
    assert us_stale == []  # 時間窓を超えた US タグは枯れる
    assert len(us_fresh) >= 1  # 新鮮な US タグは残る
    assert len(jp_stale) == 1  # JP は段階 B/C の領分＝触らない（market 安全弁）


def test_empty_selection_quiet_success(temp_db, monkeypatch) -> None:
    """選定 0 件（company_descriptions 空）は ok=True・rows=0 で静かに返し prune しない。"""
    # stale な US タグがあっても、タガーが何も再確認しない夜には枯らさない（同居設計の帰結）。
    repo.insert_themes_if_absent(["旧テーマ"], "2020-01-01T00:00:00+00:00")
    repo.upsert_stock_themes(
        [
            {
                "market": "US",
                "code": "ZZZ",
                "theme_name": "旧テーマ",
                "first_assigned_at": "2020-01-01T00:00:00+00:00",
                "last_seen_at": "2020-01-01T00:00:00+00:00",
            }
        ]
    )

    calls: list[str] = []
    monkeypatch.setattr(tag_us_themes, "tag_stock_themes", _make_fake_tagger(calls))
    result = tag_us_themes.run()

    assert result.ok is True
    assert result.rows == 0
    assert calls == []
    assert "巡回対象なし" in result.detail
    with get_engine().connect() as conn:
        remaining = repo.get_stock_themes(conn, "US", "ZZZ")
    assert len(remaining) == 1  # prune は走っていない
