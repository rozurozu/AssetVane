"""quant.notable.daily_move_pct の純関数テスト（ADR-016・ADR-067 材料①）。

設計の真実: docs/decisions.md ADR-067。DB に触れず既知系列で検証する。
"""

from __future__ import annotations

from app.quant.notable import daily_move_pct


def test_gap_up_positive() -> None:
    """前日 100 → 当日 115 は +15%（急騰＝正）。"""
    assert daily_move_pct([90.0, 100.0, 115.0]) == 115.0 / 100.0 - 1.0


def test_gap_down_negative() -> None:
    """前日 100 → 当日 88 は −12%（急落＝負・符号で向きが分かる）。"""
    result = daily_move_pct([100.0, 88.0])
    assert result is not None
    assert result < 0
    assert abs(result - (88.0 / 100.0 - 1.0)) < 1e-12


def test_uses_last_two_only() -> None:
    """当日前日比なので直近 2 本だけを使う（それ以前の値は無関係）。"""
    assert daily_move_pct([1.0, 2.0, 3.0, 100.0, 101.0]) == 101.0 / 100.0 - 1.0


def test_too_few_points_returns_none() -> None:
    """2 本未満は None（捏造しない）。"""
    assert daily_move_pct([]) is None
    assert daily_move_pct([100.0]) is None


def test_prev_zero_or_negative_returns_none() -> None:
    """前日終値が 0 以下は比率が定義できず None。"""
    assert daily_move_pct([0.0, 100.0]) is None
    assert daily_move_pct([-5.0, 100.0]) is None


def test_missing_values_return_none() -> None:
    """前日 or 当日が欠損（None）なら None。"""
    assert daily_move_pct([100.0, None]) is None
    assert daily_move_pct([None, 100.0]) is None
