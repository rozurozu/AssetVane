"""backfill_themes script の検証（ADR-050 改訂・手動フル再タグ／中断再開）。

担保: フェーズ1は company_descriptions 既存行を skip（中断再開の仕組み）・summary 無しは
書かない・ETF は対象外、フェーズ2は fetch_meta カーソル `us_themes:<symbol>` 有りを skip し
成功で前進、`--retag-all` はカーソル無視で全対象＋bump、`--limit` は各フェーズの処理上限、
`--descriptions-only` はフェーズ2を走らせない。銘柄単位の失敗は握って続行し戻り値 1
（ADR-018 の流儀）。fake adapter ＋ tag_stock_themes monkeypatch でネット・実 LLM に出ない
（testing-strategy）。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from app.advisor import theme_tagger
from app.db import repo
from app.db.engine import get_engine
from app.scripts import backfill_themes

_OLD_CURSOR = "2020-01-01T00:00:00+00:00"


class _FakeAdapter:
    """fetch_fundamentals だけ持つ fake（実 HTTP に出ない・呼び出し symbol を記録する）。"""

    def __init__(self, by_symbol: dict[str, dict[str, Any]]) -> None:
        self._by_symbol = by_symbol
        self.calls: list[str] = []

    def fetch_fundamentals(self, symbol: str) -> dict[str, Any]:
        self.calls.append(symbol)
        value = self._by_symbol.get(symbol, {})
        if value is _RAISE:
            raise RuntimeError("boom")
        return value


# fake adapter に「この symbol は例外」を指示するセンチネル。
_RAISE: dict[str, Any] = {"__raise__": True}


@pytest.fixture
def script_env(temp_db, monkeypatch) -> Iterator[None]:
    """main() の init_db（alembic）を無効化する。

    temp_db は create_schema 済みで、alembic upgrade と併用すると "table already exists" で
    落ちるため（testing-strategy の「併用しない」規約）。
    """
    monkeypatch.setattr(backfill_themes, "init_db", lambda: None)
    yield


def _setup_stocks(symbols: list[tuple[str, int | None]]) -> None:
    """us_stocks へ (symbol, is_etf) を投入するヘルパ。"""
    repo.upsert_us_stocks(
        [
            {"symbol": sym, "company_name": sym, "is_etf": is_etf, "updated_at": "t"}
            for sym, is_etf in symbols
        ]
    )


def _setup_description(code: str, text: str = "既存の説明テキスト。") -> None:
    """company_descriptions へ US 1 行を投入するヘルパ。"""
    repo.upsert_company_description(
        {
            "market": "US",
            "code": code,
            "source": "yfinance",
            "description_text": text,
            "disclosed_date": None,
            "doc_id": None,
            "fetched_at": _OLD_CURSOR,
        }
    )


def _install_adapter(monkeypatch, fake: _FakeAdapter) -> None:
    """script が組む UsEquityAdapter() を fake に差し替える。"""
    monkeypatch.setattr(backfill_themes, "UsEquityAdapter", lambda: fake)


def _install_tagger(
    monkeypatch,
    *,
    skipped_codes: frozenset[str] = frozenset(),
    failing_codes: frozenset[str] = frozenset(),
) -> list[str]:
    """tag_stock_themes を fake に差し替え、呼ばれた code のリストを返す（実 LLM に出ない）。"""
    calls: list[str] = []

    async def fake_tag(conn, *, market: str, code: str) -> dict[str, Any]:
        assert market == "US"
        calls.append(code)
        if code in failing_codes:
            raise RuntimeError("llm boom")
        if code in skipped_codes:
            return {"code": code, "themes": [], "skipped": True}
        return {"code": code, "themes": ["生成AI"], "n_new_themes": 0}

    monkeypatch.setattr(theme_tagger, "tag_stock_themes", fake_tag)
    return calls


def _theme_cursor(code: str) -> str | None:
    """fetch_meta カーソル `us_themes:<code>` の last_fetched_date（無ければ None）。"""
    with get_engine().connect() as conn:
        meta = repo.get_fetch_meta(conn, f"us_themes:{code}")
    return meta.get("last_fetched_date") if meta else None


def test_phase1_skips_existing_and_etf(script_env, monkeypatch) -> None:
    """既存 company_descriptions 行のある symbol と ETF は adapter が呼ばれない（中断再開）。"""
    _setup_stocks([("AAA", 0), ("BBB", None), ("EEE", 1)])  # is_etf NULL は普通株扱い
    _setup_description("AAA", "AAA の既存説明。")
    fake = _FakeAdapter(
        {
            "BBB": {"business_summary": "BBB makes widgets."},
            "EEE": {"business_summary": "ETF summary (should not be fetched)."},
        }
    )
    _install_adapter(monkeypatch, fake)

    rc = backfill_themes.main(["--descriptions-only"])
    assert rc == 0
    # 既存行のある AAA・ETF の EEE は呼ばれず、未取得の BBB だけ取得される。
    assert fake.calls == ["BBB"]

    with get_engine().connect() as conn:
        aaa = repo.get_company_description(conn, "US", "AAA")
        bbb = repo.get_company_description(conn, "US", "BBB")
        eee = repo.get_company_description(conn, "US", "EEE")
    assert aaa is not None and aaa["description_text"] == "AAA の既存説明。"  # 据え置き
    assert bbb is not None and bbb["description_text"] == "BBB makes widgets."
    assert eee is None


def test_phase1_no_summary_not_written(script_env, monkeypatch) -> None:
    """business_summary 欠損/空白のみの銘柄は company_descriptions に書かれない（ADR-014）。"""
    _setup_stocks([("AAA", 0), ("BBB", 0)])
    fake = _FakeAdapter({"AAA": {}, "BBB": {"business_summary": "   "}})
    _install_adapter(monkeypatch, fake)

    rc = backfill_themes.main(["--descriptions-only"])
    assert rc == 0  # summary 無しは失敗ではなく skip
    with get_engine().connect() as conn:
        assert repo.get_company_description(conn, "US", "AAA") is None
        assert repo.get_company_description(conn, "US", "BBB") is None


def test_phase1_failure_continues(script_env, monkeypatch) -> None:
    """1 銘柄の取得失敗は握って続行し、戻り値は 1（ADR-018 の流儀）。"""
    _setup_stocks([("BAD", 0), ("BBB", 0)])
    fake = _FakeAdapter({"BAD": _RAISE, "BBB": {"business_summary": "BBB makes widgets."}})
    _install_adapter(monkeypatch, fake)

    rc = backfill_themes.main(["--descriptions-only"])
    assert rc == 1
    assert fake.calls == ["BAD", "BBB"]  # 失敗しても後続を止めない
    with get_engine().connect() as conn:
        bbb = repo.get_company_description(conn, "US", "BBB")
    assert bbb is not None  # 成功した銘柄は書けている


def test_phase2_cursor_skip_and_advance(script_env, monkeypatch) -> None:
    """カーソル有り symbol は tagger が呼ばれず、成功した symbol はカーソルが前進する。"""
    _setup_stocks([("AAA", 0), ("BBB", 0)])
    _setup_description("AAA")
    _setup_description("BBB")
    repo.upsert_fetch_meta("us_themes:AAA", _OLD_CURSOR)  # AAA はタグ付け済み
    _install_adapter(monkeypatch, _FakeAdapter({}))  # フェーズ1の対象は無し
    calls = _install_tagger(monkeypatch)

    rc = backfill_themes.main([])
    assert rc == 0
    assert calls == ["BBB"]  # カーソル有りの AAA は呼ばれない（中断再開点）
    # 成功した BBB のカーソルは ISO datetime で前進する。
    cursor = _theme_cursor("BBB")
    assert cursor is not None and cursor > _OLD_CURSOR
    assert _theme_cursor("AAA") == _OLD_CURSOR  # 据え置き
    # 種テーマが目録へ投入されている（冪等）。
    with get_engine().connect() as conn:
        assert "生成AI" in repo.list_theme_names(conn)


def test_phase2_skipped_does_not_advance_cursor(script_env, monkeypatch) -> None:
    """tagger が skip（説明テキスト無し）した symbol はカーソルを進めない（再実行で拾い直す）。"""
    _setup_stocks([("CCC", 0)])
    _setup_description("CCC", text="")  # 行はあるがテキスト空 → tagger が skip する状況
    _install_adapter(monkeypatch, _FakeAdapter({}))
    calls = _install_tagger(monkeypatch, skipped_codes=frozenset({"CCC"}))

    rc = backfill_themes.main([])
    assert rc == 0
    assert calls == ["CCC"]
    assert _theme_cursor("CCC") is None  # カーソルは進まない


def test_retag_all_ignores_cursor_and_bumps(script_env, monkeypatch) -> None:
    """--retag-all はカーソル有りでも tagger を呼び、カーソルを bump する（手動フル再タグ）。"""
    _setup_stocks([("AAA", 0), ("BBB", 0)])
    _setup_description("AAA")
    _setup_description("BBB")
    repo.upsert_fetch_meta("us_themes:AAA", _OLD_CURSOR)
    repo.upsert_fetch_meta("us_themes:BBB", _OLD_CURSOR)
    _install_adapter(monkeypatch, _FakeAdapter({}))
    calls = _install_tagger(monkeypatch)

    rc = backfill_themes.main(["--retag-all"])
    assert rc == 0
    assert sorted(calls) == ["AAA", "BBB"]  # カーソル有りでも全対象を回す
    for code in ("AAA", "BBB"):
        cursor = _theme_cursor(code)
        assert cursor is not None and cursor > _OLD_CURSOR  # bump されている


def test_limit_caps_each_phase(script_env, monkeypatch) -> None:
    """--limit は各フェーズの処理銘柄数を制限する（試走・コスト見積もり用）。"""
    _setup_stocks([("AAA", 0), ("BBB", 0), ("CCC", 0)])
    fake = _FakeAdapter({s: {"business_summary": f"{s} summary."} for s in ("AAA", "BBB", "CCC")})
    _install_adapter(monkeypatch, fake)
    calls = _install_tagger(monkeypatch)

    rc = backfill_themes.main(["--limit", "1"])
    assert rc == 0
    assert len(fake.calls) == 1  # フェーズ1 は 1 件だけ取得
    assert len(calls) == 1  # フェーズ2 も 1 件だけタグ付け


def test_descriptions_only_skips_phase2(script_env, monkeypatch) -> None:
    """--descriptions-only ではフェーズ2（タグ付け）が走らない。"""
    _setup_stocks([("BBB", 0)])
    _setup_description("BBB")  # フェーズ2 対象になり得る未タグ銘柄
    _install_adapter(monkeypatch, _FakeAdapter({}))
    calls = _install_tagger(monkeypatch)

    rc = backfill_themes.main(["--descriptions-only"])
    assert rc == 0
    assert calls == []  # tagger は一度も呼ばれない
    assert _theme_cursor("BBB") is None


def test_phase2_failure_continues(script_env, monkeypatch) -> None:
    """1 銘柄のタグ付け失敗は握って続行し、失敗銘柄のカーソルは進めない（ADR-018）。"""
    _setup_stocks([("BBB", 0), ("CCC", 0)])
    _setup_description("BBB")
    _setup_description("CCC")
    _install_adapter(monkeypatch, _FakeAdapter({}))
    calls = _install_tagger(monkeypatch, failing_codes=frozenset({"BBB"}))

    rc = backfill_themes.main([])
    assert rc == 1
    assert sorted(calls) == ["BBB", "CCC"]  # 失敗しても後続を止めない
    assert _theme_cursor("BBB") is None  # 失敗銘柄は再実行で拾い直される
    assert _theme_cursor("CCC") is not None  # 成功銘柄は前進
