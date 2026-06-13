"""resolve_differential_start 純関数の単体テスト（batch-pattern／ADR-018）。

担保: last_fetched=None なら backfill_start をそのまま返す（初回）・last_fetched ありなら
overlap_days 日重ねた地点を返す（鮮度プローブ）・overlap_days 可変が効く・番兵 backfill_start
（fund_navs の全履歴）がそのまま返る・月初をまたぐ重ねが date 演算で正しいこと。純関数なので
DB・ネットに触れず入出力直書きで検証する（testing-strategy）。
"""

from __future__ import annotations

from datetime import date, timedelta

from app.batch.jobs._cursor import DEFAULT_OVERLAP_DAYS, resolve_differential_start


def test_no_meta_returns_backfill_start() -> None:
    """last_fetched=None（初回）は backfill_start をそのまま返す。"""
    assert resolve_differential_start(None, backfill_start="2024-06-08") == "2024-06-08"


def test_last_fetched_overlaps_by_default_days() -> None:
    """last_fetched ありは既定 overlap 日数だけ重ねた地点を返す（鮮度プローブ）。"""
    start = resolve_differential_start("2026-06-05", backfill_start="2024-06-08")
    assert start == (date(2026, 6, 5) - timedelta(days=DEFAULT_OVERLAP_DAYS)).isoformat()
    assert start <= "2026-06-05"  # 最終取得日に重なる


def test_overlap_days_is_configurable() -> None:
    """overlap_days 引数で重ね日数を変えられる（0 なら最終取得日そのもの）。"""
    assert (
        resolve_differential_start("2026-06-10", backfill_start="x", overlap_days=3) == "2026-06-07"
    )
    assert (
        resolve_differential_start("2026-06-10", backfill_start="x", overlap_days=0) == "2026-06-10"
    )


def test_sentinel_backfill_start_passes_through() -> None:
    """初回番兵 '1900-01-01'（fund_navs の全履歴）も backfill_start としてそのまま返る。"""
    assert resolve_differential_start(None, backfill_start="1900-01-01") == "1900-01-01"


def test_overlap_crosses_month_boundary() -> None:
    """月初をまたぐ重ねでも date 演算で正しく前月へ戻る。"""
    start = resolve_differential_start("2026-06-02", backfill_start="x", overlap_days=5)
    assert start == "2026-05-28"
