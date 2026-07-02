"""quant.outcome の採点純関数を担保する（ADR-077・提示ベースの提案採点）。

compute_horizon_outcome が系列カウントで entry/exit を選び実現/超過リターンを出すこと・境界
（起点無し/horizon 未到達/非営業日起点/NaN/ベンチ欠測/entry<=0）で安全に pending に倒すこと、
classify_hit の buy/sell/notable/フォールバック規則を、DataFrame/list 直書きで検証する
（DB も today も要らない純関数＝testing-strategy）。
"""

from __future__ import annotations

import math

import pytest

from app.quant.outcome import classify_hit, compute_horizon_outcome


def _prices(pairs: list[tuple[str, float | None]]) -> list[dict[str, object]]:
    return [{"date": d, "adj_close": c} for d, c in pairs]


def _bench(pairs: list[tuple[str, float | None]]) -> list[dict[str, object]]:
    return [{"date": d, "close": c} for d, c in pairs]


def test_final_realized_and_excess_basic():
    """起点=提案日ちょうど・horizon 到達で realized/excess が手計算値に一致する（final）。"""
    prices = _prices([("2026-01-05", 100.0), ("2026-01-06", 110.0), ("2026-01-07", 120.0)])
    bench = _bench([("2026-01-05", 200.0), ("2026-01-06", 202.0), ("2026-01-07", 210.0)])
    out = compute_horizon_outcome(prices, bench, entry_date="2026-01-05", horizon=2)

    assert out["status"] == "final"
    assert out["entry_priced_date"] == "2026-01-05"
    assert out["as_of_date"] == "2026-01-07"
    assert out["realized_return"] == pytest.approx(120.0 / 100.0 - 1.0)
    # excess = 銘柄実現 - ベンチ実現 = 0.20 - (210/200 - 1) = 0.20 - 0.05
    assert out["excess_return"] == pytest.approx(0.20 - 0.05)
    assert out["benchmark_fallback"] is False


def test_entry_date_on_holiday_moves_forward():
    """提案日が休場（系列に無い）なら翌営業日のバーを起点に採る（forward・ADR-077 決定③）。"""
    prices = _prices([("2026-01-05", 100.0), ("2026-01-06", 110.0), ("2026-01-07", 121.0)])
    # 2026-01-04 は系列に無い（休場想定）→ 起点は 01-05。
    out = compute_horizon_outcome(prices, None, entry_date="2026-01-04", horizon=2)
    assert out["status"] == "final"
    assert out["entry_priced_date"] == "2026-01-05"
    assert out["realized_return"] == pytest.approx(1.21 - 1.0)


def test_horizon_not_reached_is_pending_with_entry():
    """到達バーが無い（horizon 未経過）なら pending だが起点情報は持つ（翌晩以降 final へ）。"""
    prices = _prices([("2026-01-05", 100.0), ("2026-01-06", 110.0)])
    out = compute_horizon_outcome(prices, None, entry_date="2026-01-05", horizon=20)
    assert out["status"] == "pending"
    assert out["entry_priced_date"] == "2026-01-05"
    assert out["entry_price"] == pytest.approx(100.0)
    assert out["as_of_date"] is None
    assert out["realized_return"] is None


def test_entry_bar_absent_is_pending():
    """全バーが提案日より前＝起点バーがまだ無い → pending（entry も None）。"""
    prices = _prices([("2026-01-05", 100.0), ("2026-01-06", 110.0)])
    out = compute_horizon_outcome(prices, None, entry_date="2026-02-01", horizon=1)
    assert out["status"] == "pending"
    assert out["entry_priced_date"] is None
    assert out["entry_price"] is None


def test_entry_price_non_positive_is_pending():
    """起点バーの adj_close が 0 以下＝比率不能 → pending（捏造しない・ADR-014）。"""
    prices = _prices([("2026-01-05", 0.0), ("2026-01-06", 110.0)])
    out = compute_horizon_outcome(prices, None, entry_date="2026-01-05", horizon=1)
    assert out["status"] == "pending"


def test_exit_price_nan_is_pending():
    """到達バーの adj_close が NaN なら pending（欠測を埋めない）。"""
    prices = _prices([("2026-01-05", 100.0), ("2026-01-06", math.nan)])
    out = compute_horizon_outcome(prices, None, entry_date="2026-01-05", horizon=1)
    assert out["status"] == "pending"
    assert out["entry_priced_date"] == "2026-01-05"


def test_benchmark_missing_dates_falls_back_to_absolute():
    """ベンチが起点/到達日に無ければ excess=None・benchmark_fallback=True（final は保つ）。"""
    prices = _prices([("2026-01-05", 100.0), ("2026-01-06", 110.0)])
    bench = _bench([("2025-12-01", 200.0)])  # 対象日と重ならない
    out = compute_horizon_outcome(prices, bench, entry_date="2026-01-05", horizon=1)
    assert out["status"] == "final"
    assert out["realized_return"] == pytest.approx(0.10)
    assert out["excess_return"] is None
    assert out["benchmark_fallback"] is True


def test_benchmark_none_falls_back_to_absolute():
    """benchmark=None（ベンチ未提供）でも final・excess=None・fallback=True。"""
    prices = _prices([("2026-01-05", 100.0), ("2026-01-06", 90.0)])
    out = compute_horizon_outcome(prices, None, entry_date="2026-01-05", horizon=1)
    assert out["status"] == "final"
    assert out["realized_return"] == pytest.approx(-0.10)
    assert out["excess_return"] is None
    assert out["benchmark_fallback"] is True


def test_classify_hit_directional_and_fallback():
    """classify_hit: buy→excess>0、sell→excess<0、notable→None、excess=None は realized で判定。"""
    assert classify_hit("buy", 0.02, 0.05) is True
    assert classify_hit("buy", -0.01, 0.05) is False  # excess 優先
    assert classify_hit("sell", -0.02, 0.03) is True
    assert classify_hit("sell", 0.02, -0.03) is False
    assert classify_hit("notable", 0.05, 0.05) is None  # 非方向は常に None
    assert classify_hit("buy", None, 0.04) is True  # excess 欠測は realized にフォールバック
    assert classify_hit("buy", None, None) is None  # pending
