"""営業日候補生成の純ロジックを固定する（spec §3.4・§8）。

土日除外と範囲の境界（両端含む・start>end で空）を検証する。ネット/DB に触れない。
"""

from __future__ import annotations

from app.batch.calendar import candidate_days


def test_excludes_weekends() -> None:
    # 2026-06-01(月)〜2026-06-07(日)。土(6)・日(7)を除外して平日5日が出る。
    days = list(candidate_days("2026-06-01", "2026-06-07"))
    assert days == [
        "2026-06-01",
        "2026-06-02",
        "2026-06-03",
        "2026-06-04",
        "2026-06-05",
    ]


def test_inclusive_range_single_weekday() -> None:
    # 両端含む。平日 1 日のみ。
    assert list(candidate_days("2026-06-03", "2026-06-03")) == ["2026-06-03"]


def test_single_weekend_day_empty() -> None:
    # 土曜のみ → 空。
    assert list(candidate_days("2026-06-06", "2026-06-06")) == []


def test_start_after_end_empty() -> None:
    # start > end は何も yield しない。
    assert list(candidate_days("2026-06-10", "2026-06-01")) == []


def test_spans_multiple_weeks() -> None:
    # 2 週またぎで土日が 2 回ぶん除外される（全カレンダー日 14・営業日 10）。
    days = list(candidate_days("2026-06-01", "2026-06-14"))
    assert len(days) == 10
    # 土日が含まれないことを確認。
    weekends = {"2026-06-06", "2026-06-07", "2026-06-13", "2026-06-14"}
    assert weekends.isdisjoint(days)
