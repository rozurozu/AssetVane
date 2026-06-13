"""fetch_fund_navs ジョブの単体テスト（ADR-018/054・ネット非依存）。

担保:
- 差分カーソル（_start_date_for_isin）: fetch_meta 未存在は番兵 '1900-01-01'（全履歴）／
  fetch_meta ありは DEFAULT_OVERLAP_DAYS 日重ねた地点（鮮度プローブ・_cursor.py に集約後の回帰）。
- run() の総崩れ判定（review-2026-06-12 §3）: funds 0 件は skip／NAV 取得→UPSERT＋fetch_meta 前進／
  空配列でも today まで前進／一部失敗は ok=True（detail に取得不可）／
  **全 ISIN 失敗のときだけ ok=False**。
fake adapter 注入（FundNavAdapter を patch）で実 HTTP・実 LLM に出ない。temp_db で本物 DB に触れない
（testing-strategy・fetch_index 系テストのミラー）。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from unittest.mock import patch

from app.adapters.fund_nav import FundNavFetchError
from app.batch.jobs import fetch_fund_navs
from app.batch.jobs._cursor import DEFAULT_OVERLAP_DAYS
from app.db import repo
from app.db.engine import get_engine

# ------------------------------------------------------------------ 差分カーソル


def test_start_date_for_isin_sentinel_when_no_meta(temp_db) -> None:
    """fetch_meta 未存在（初回）は全履歴の番兵 '1900-01-01' を返す。"""
    assert fetch_fund_navs._start_date_for_isin("JP90C000H1T1") == "1900-01-01"


def test_start_date_for_isin_overlaps_last_fetched(temp_db) -> None:
    """fetch_meta あり→last_fetched_date に重ねた地点を返す（鮮度プローブ・C-1）。"""
    repo.upsert_fetch_meta("fund_navs:JP90C000H1T1", "2026-06-05")
    start = fetch_fund_navs._start_date_for_isin("JP90C000H1T1")
    assert start == (date(2026, 6, 5) - timedelta(days=DEFAULT_OVERLAP_DAYS)).isoformat()


# ------------------------------------------------------------------ run() 総崩れ判定


def test_run_skips_when_no_funds(temp_db) -> None:
    """登録済み投信 0 件なら adapter を作らず ok=True・rows=0（スキップ）。"""
    with patch("app.batch.jobs.fetch_fund_navs.FundNavAdapter") as MockAdapter:
        result = fetch_fund_navs.run()

    assert result.ok is True
    assert result.rows == 0
    assert "登録済み投信なし" in result.detail
    MockAdapter.assert_not_called()  # funds 0 件なら adapter を構築しない


def test_run_upserts_navs_and_advances_meta(temp_db) -> None:
    """NAV を UPSERT し fetch_meta を最新取得日に前進させる（ADR-054）。"""
    repo.upsert_fund("JP90C000H1T1", "テスト投信", assoc_code="03311179")
    rows = [
        {"isin": "JP90C000H1T1", "date": "2026-06-04", "nav": 12345.0},
        {"isin": "JP90C000H1T1", "date": "2026-06-05", "nav": 12400.0},
    ]
    with patch("app.batch.jobs.fetch_fund_navs.FundNavAdapter") as MockAdapter:
        MockAdapter.return_value.fetch_nav_history.return_value = rows
        result = fetch_fund_navs.run()

    assert result.ok is True
    assert result.rows == 2
    with get_engine().connect() as conn:
        series = repo.get_fund_nav_series(conn, "JP90C000H1T1")
        meta = repo.get_fetch_meta(conn, "fund_navs:JP90C000H1T1")
    assert len(series) == 2
    assert meta is not None
    assert meta["last_fetched_date"] == "2026-06-05"  # 最新取得日へ前進
    assert meta["last_attempt_ok"] == 1


def test_run_empty_rows_advances_meta_to_today(temp_db) -> None:
    """空配列でも fetch_meta を today まで前進させ（空振り防止）attempt は成功扱い。"""
    repo.upsert_fund("JP90C000H1T1", "テスト投信", assoc_code="03311179")
    with patch("app.batch.jobs.fetch_fund_navs.FundNavAdapter") as MockAdapter:
        MockAdapter.return_value.fetch_nav_history.return_value = []
        result = fetch_fund_navs.run()

    assert result.ok is True
    assert result.rows == 0
    today = date.today().isoformat()
    with get_engine().connect() as conn:
        meta = repo.get_fetch_meta(conn, "fund_navs:JP90C000H1T1")
    assert meta is not None
    assert meta["last_fetched_date"] == today
    assert meta["last_attempt_ok"] == 1


def test_run_partial_failure_is_ok(temp_db) -> None:
    """一部 ISIN が失敗しても 1 本でも成功すれば ok=True（取得不可は detail に残す）。"""
    repo.upsert_fund("JP90C000GOOD", "成功投信", assoc_code="0001")
    repo.upsert_fund("JP90C000BADX", "失敗投信", assoc_code="0002")
    good_rows = [{"isin": "JP90C000GOOD", "date": "2026-06-05", "nav": 10000.0}]

    def fake_fetch(isin: str, **kwargs: Any) -> list[dict[str, Any]]:
        if isin == "JP90C000GOOD":
            return good_rows
        raise FundNavFetchError("協会コード不正で CSV 空")

    with patch("app.batch.jobs.fetch_fund_navs.FundNavAdapter") as MockAdapter:
        MockAdapter.return_value.fetch_nav_history.side_effect = fake_fetch
        result = fetch_fund_navs.run()

    assert result.ok is True  # 全滅ではないので失敗扱いにしない
    assert result.rows == 1
    assert "JP90C000BADX" in result.detail
    assert "取得不可" in result.detail
    # 失敗 ISIN は last_attempt_ok=0・last_fetched_date 据え置き（digest が拾う）。
    with get_engine().connect() as conn:
        bad_meta = repo.get_fetch_meta(conn, "fund_navs:JP90C000BADX")
    assert bad_meta is not None and bad_meta["last_attempt_ok"] == 0
    assert bad_meta["last_fetched_date"] is None


def test_run_all_fail_returns_failure(temp_db) -> None:
    """試行した全 ISIN が失敗（総崩れ）したときだけ ok=False（ADR-018）。"""
    repo.upsert_fund("JP90C000AAAA", "投信A", assoc_code="0001")
    repo.upsert_fund("JP90C000BBBB", "投信B", assoc_code="0002")
    with patch("app.batch.jobs.fetch_fund_navs.FundNavAdapter") as MockAdapter:
        MockAdapter.return_value.fetch_nav_history.side_effect = FundNavFetchError("CSV 空")
        result = fetch_fund_navs.run()

    assert result.ok is False
    assert "全 2 投信取得失敗" in result.detail
    assert "JP90C000AAAA" in result.detail
    assert "JP90C000BBBB" in result.detail
