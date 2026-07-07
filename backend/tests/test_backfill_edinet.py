"""backfill_edinet script の検証（テーマタグ段階 C・ADR-056・review-2026-06-12 §3 のテスト穴）。

担保: 公式 EDINET 未登録（DB の edinet_config が空＝ADR-087）は return 2（クロールしない）・
`--from` は窓/カーソルより優先・
カーソル無しは窓頭（today − window）起点・**カーソル有りは cursor+1 から再開（中断再開）**・
start > today は未クロールなしで return 0（crawl 不発）・crawl の失敗ありは return 1／無しは
return 0・KeyboardInterrupt は return 130（再実行で続きから）。crawl と _today_jst を fake に
差し替えてネット・実 LLM に出ない（backfill_themes テストのミラー・testing-strategy）。
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from app.batch.jobs import fetch_edinet_descriptions as edinet_job
from app.db import repo
from app.db.engine import get_engine
from app.scripts import backfill_edinet

_CRAWL_SOURCE = "edinet:crawl"
_TODAY = date(2026, 6, 13)


def _result(**overrides: Any) -> dict[str, Any]:
    """crawl の戻り値スタブ（main の print が参照する全キーを満たす）。"""
    base: dict[str, Any] = {
        "dates_done": 1,
        "n_summarized": 0,
        "n_skip_dossier": 0,
        "n_skip_existing": 0,
        "n_no_business": 0,
        "failures": [],
        "last_cursor": _TODAY.isoformat(),
        "cap_reached": False,
    }
    base.update(overrides)
    return base


@pytest.fixture
def script_env(temp_db, monkeypatch) -> dict[str, Any]:
    """main() の副作用を封じる: init_db 無効化・公式 EDINET キー seed・today 固定・crawl fake 化。

    temp_db は create_schema 済みで、init_db（alembic upgrade）と併用すると "table already
    exists" で落ちる（testing-strategy）。公式 EDINET の接続値は DB 解決＝ADR-087 なので
    ダミーキーを撒いて「設定済み」にする。_resolve_start は実 DB（temp_db）の fetch_meta を読むため
    差し替えず、カーソルは fetch_meta を撒いて検証。crawl は呼び出し kwargs を captured に記録。
    """
    monkeypatch.setattr(backfill_edinet, "init_db", lambda: None)
    with get_engine().begin() as conn:
        repo.upsert_edinet_config(conn, {"api_key": "dummy-key"})
    monkeypatch.setattr(backfill_edinet, "_today_jst", lambda: _TODAY)

    captured: dict[str, Any] = {"calls": [], "result": _result(), "raise": None}

    def fake_crawl(**kwargs: Any) -> dict[str, Any]:
        captured["calls"].append(kwargs)
        if captured["raise"] is not None:
            raise captured["raise"]
        return captured["result"]

    monkeypatch.setattr(backfill_edinet, "crawl", fake_crawl)
    return captured


def test_main_missing_api_key_returns_2(script_env) -> None:
    """公式 EDINET 未登録（DB の api_key 空）は return 2 でクロールに進まない（ADR-087）。"""
    with get_engine().begin() as conn:
        repo.upsert_edinet_config(conn, {"api_key": ""})  # 設定済み seed を空に上書き

    rc = backfill_edinet.main([])

    assert rc == 2
    assert script_env["calls"] == []  # クロールは一度も呼ばれない


def test_main_from_overrides_window_and_cursor(script_env) -> None:
    """`--from` はカーソル/窓より優先して開始提出日になる。"""
    # カーソルがあっても --from が勝つことを示すため fetch_meta を撒く。
    repo.upsert_fetch_meta(_CRAWL_SOURCE, "2026-01-01")

    rc = backfill_edinet.main(["--from", "2025-09-15"])

    assert rc == 0
    assert script_env["calls"][0]["start_date"] == date(2025, 9, 15)
    assert script_env["calls"][0]["end_date"] == _TODAY


def test_main_no_cursor_uses_window_head(script_env) -> None:
    """カーソル無しは today − window-days を起点にクロールする（初回は窓頭から）。"""
    rc = backfill_edinet.main(["--window-days", "30"])

    assert rc == 0
    assert script_env["calls"][0]["start_date"] == date(2026, 5, 14)  # 2026-06-13 − 30 日


def test_main_resumes_from_cursor(script_env) -> None:
    """カーソル有りは cursor+1 から再開する（中断再開・窓は初回のみ効く）。"""
    repo.upsert_fetch_meta(_CRAWL_SOURCE, "2026-06-01")

    rc = backfill_edinet.main(["--window-days", "30"])

    assert rc == 0
    assert script_env["calls"][0]["start_date"] == date(2026, 6, 2)  # cursor 翌日から


def test_main_start_after_today_returns_0_without_crawl(script_env) -> None:
    """カーソルが today まで進んでいれば start > today で未クロールなし・crawl しない。"""
    repo.upsert_fetch_meta(_CRAWL_SOURCE, _TODAY.isoformat())  # 翌日 = 2026-06-14 > today

    rc = backfill_edinet.main([])

    assert rc == 0
    assert script_env["calls"] == []  # 進めるべき提出日が無いので crawl 不発


def test_main_passes_limit_as_cap(script_env) -> None:
    """`--limit` は crawl の cap に渡る（試走・コスト見積もり）。"""
    rc = backfill_edinet.main(["--from", "2025-09-15", "--limit", "50"])

    assert rc == 0
    assert script_env["calls"][0]["cap"] == 50


def test_main_failures_returns_1(script_env) -> None:
    """crawl が失敗 doc を返したら return 1（再実行で拾い直し）。"""
    script_env["result"] = _result(failures=["S100: boom"], n_summarized=3)

    rc = backfill_edinet.main(["--from", "2025-09-15"])

    assert rc == 1


def test_main_success_returns_0(script_env) -> None:
    """crawl が失敗なしで完了したら return 0。"""
    script_env["result"] = _result(n_summarized=5, dates_done=10)

    rc = backfill_edinet.main(["--from", "2025-09-15"])

    assert rc == 0


def test_main_keyboard_interrupt_returns_130(script_env) -> None:
    """クロール中の中断（Ctrl-C）は return 130（再実行すればカーソルから続く）。"""
    script_env["raise"] = KeyboardInterrupt()

    rc = backfill_edinet.main(["--from", "2025-09-15"])

    assert rc == 130


def test_resolve_start_resumes_from_cursor_plus_one(temp_db) -> None:
    """_resolve_start: カーソル有りは翌日・無しは fallback（中断再開の核を直接担保）。"""
    fallback = date(2025, 3, 1)
    assert edinet_job._resolve_start(no_cursor_fallback=fallback) == fallback
    repo.upsert_fetch_meta(_CRAWL_SOURCE, "2026-06-10")
    assert edinet_job._resolve_start(no_cursor_fallback=fallback) == date(2026, 6, 11)
