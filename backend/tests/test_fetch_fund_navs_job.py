"""fetch_fund_navs の差分カーソル（_start_date_for_isin）の単体テスト（ADR-018/054）。

担保: fetch_meta 未存在は番兵 '1900-01-01'（全履歴）を返す・fetch_meta ありは
DEFAULT_OVERLAP_DAYS 日重ねた地点を返す（鮮度プローブ・_cursor.py に集約した後の回帰）。
temp_db を使い実 DB・実ネットに触れない（testing-strategy）。
"""

from __future__ import annotations

from datetime import date, timedelta

from app.batch.jobs import fetch_fund_navs
from app.batch.jobs._cursor import DEFAULT_OVERLAP_DAYS
from app.db import repo


def test_start_date_for_isin_sentinel_when_no_meta(temp_db) -> None:
    """fetch_meta 未存在（初回）は全履歴の番兵 '1900-01-01' を返す。"""
    assert fetch_fund_navs._start_date_for_isin("JP90C000H1T1") == "1900-01-01"


def test_start_date_for_isin_overlaps_last_fetched(temp_db) -> None:
    """fetch_meta あり→last_fetched_date に重ねた地点を返す（鮮度プローブ・C-1）。"""
    repo.upsert_fetch_meta("fund_navs:JP90C000H1T1", "2026-06-05")
    start = fetch_fund_navs._start_date_for_isin("JP90C000H1T1")
    assert start == (date(2026, 6, 5) - timedelta(days=DEFAULT_OVERLAP_DAYS)).isoformat()
