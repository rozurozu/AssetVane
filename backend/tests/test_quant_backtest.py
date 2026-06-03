"""quant.backtest の既知系列テスト（ADR-016: テスト済みコードで実装）。

設計の真実: docs/phase-specs/phase2-spec.md §4.4・§8。
DB に触れず手組み DataFrame/Series で純関数を検証する（実 API も叩かない）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.quant.backtest import backtest_portfolio

# ---------------------------------------------------------------------------
# ヘルパ
# ---------------------------------------------------------------------------


def _panel_and_bench(
    port_prices: dict[str, list[float]],
    bench_prices: list[float],
    base_date: str = "2024-01-01",
) -> tuple[pd.DataFrame, pd.Series]:
    """portfolio panel と benchmark Series を同じ日付 index で作る。"""
    n = max(len(v) for v in port_prices.values())
    dates = pd.date_range(base_date, periods=n, freq="B")
    panel = pd.DataFrame(port_prices, index=dates)
    bench = pd.Series(bench_prices, index=dates[: len(bench_prices)])
    return panel, bench


# ---------------------------------------------------------------------------
# buy&hold 累積リターンが手計算と一致
# ---------------------------------------------------------------------------


def test_buy_and_hold_cumulative_return_matches_manual() -> None:
    """buy&hold の累積リターンが手計算と一致する。

    1 銘柄（ウェイト=1）で 100 → 120 の直線 adj_close（10 点）。
    pct_change() の各ステップは隣接要素の比（r[i] = p[i]/p[i-1] - 1）。
    prod(1+r) = prices[-1] / prices[0] なので、
    累積リターン = prices[-1] / prices[0] - 1 = 120/100 - 1 = 0.20。
    """
    n = 10
    prices = list(np.linspace(100.0, 120.0, n))
    bench = list(np.linspace(1000.0, 1050.0, n))
    panel, benchmark = _panel_and_bench({"A": prices}, bench)

    result = backtest_portfolio(panel, {"A": 1.0}, benchmark)

    # pct_change() の各ステップは隣接要素の比（r[i] = p[i]/p[i-1] - 1）。
    # prod(1+r) = prices[-1] / prices[0]。
    # よって cumulative_return = prices[-1] / prices[0] - 1 = 120/100 - 1 = 0.20。
    expected_cum = prices[-1] / prices[0] - 1.0
    assert abs(result["portfolio"]["cumulative_return"] - expected_cum) < 1e-9


# ---------------------------------------------------------------------------
# excess_return の符号が正しい
# ---------------------------------------------------------------------------


def test_excess_return_positive_when_portfolio_outperforms() -> None:
    """ポートフォリオ > ベンチマークのとき excess_return > 0。"""
    n = 100
    # ポート: 毎日 +0.5% の急成長
    port_r = np.full(n, 0.005)
    port_p = np.cumprod(1.0 + port_r) * 100.0
    # ベンチ: 毎日 +0.1% の緩成長
    bench_r = np.full(n, 0.001)
    bench_p = np.cumprod(1.0 + bench_r) * 1000.0

    panel, benchmark = _panel_and_bench({"A": list(port_p)}, list(bench_p))
    result = backtest_portfolio(panel, {"A": 1.0}, benchmark)
    assert result["excess_return"] > 0.0


def test_excess_return_negative_when_portfolio_underperforms() -> None:
    """ポートフォリオ < ベンチマークのとき excess_return < 0。"""
    n = 100
    # ポート: 毎日 +0.1% の緩成長
    port_r = np.full(n, 0.001)
    port_p = np.cumprod(1.0 + port_r) * 100.0
    # ベンチ: 毎日 +0.5% の急成長
    bench_r = np.full(n, 0.005)
    bench_p = np.cumprod(1.0 + bench_r) * 1000.0

    panel, benchmark = _panel_and_bench({"A": list(port_p)}, list(bench_p))
    result = backtest_portfolio(panel, {"A": 1.0}, benchmark)
    assert result["excess_return"] < 0.0


# ---------------------------------------------------------------------------
# 2 資産の手計算検証
# ---------------------------------------------------------------------------


def test_two_asset_portfolio_return() -> None:
    """2 資産・均等ウェイトの年率リターンが手計算と ±0.01 一致する。"""
    np.random.seed(7)
    n = 300
    r_a = np.random.normal(0.001, 0.01, n)
    r_b = np.random.normal(0.002, 0.01, n)
    p_a = np.cumprod(1.0 + r_a) * 100.0
    p_b = np.cumprod(1.0 + r_b) * 100.0
    bench_r = np.random.normal(0.0005, 0.01, n)
    bench_p = np.cumprod(1.0 + bench_r) * 1000.0

    panel, benchmark = _panel_and_bench({"A": list(p_a), "B": list(p_b)}, list(bench_p))
    result = backtest_portfolio(panel, {"A": 0.5, "B": 0.5}, benchmark)

    # 手計算（同日列で整合）
    common_idx = pd.date_range("2024-01-01", periods=n, freq="B")
    ret_a = pd.Series(p_a, index=common_idx).pct_change().dropna()
    ret_b = pd.Series(p_b, index=common_idx).pct_change().dropna()
    port_ret = 0.5 * ret_a + 0.5 * ret_b
    # 積集合で揃える（同じ index なので変わらず）
    expected_ann = float(port_ret.mean() * 252)

    assert abs(result["portfolio"]["annual_return"] - expected_ann) < 0.01


# ---------------------------------------------------------------------------
# curve が 1 始まりの累積値になっている
# ---------------------------------------------------------------------------


def test_curve_starts_at_one() -> None:
    """曲線の最初の value が 1.0 前後（累積リターン = 1 始まり）。"""
    prices = list(np.linspace(100.0, 130.0, 50))
    bench = list(np.linspace(1000.0, 1100.0, 50))
    panel, benchmark = _panel_and_bench({"A": prices}, bench)
    result = backtest_portfolio(panel, {"A": 1.0}, benchmark)

    port_curve = result["portfolio"]["curve"]
    assert len(port_curve) > 0
    # pct_change().dropna() で先頭1行消えるため、curve[0].value は初回リターン後の累積値
    # 1 始まりで最初のステップが加算された値が入っているはず（≒ 1 付近）
    assert 0.90 <= port_curve[0]["value"] <= 1.15


# ---------------------------------------------------------------------------
# max_drawdown の符号が負（または 0）
# ---------------------------------------------------------------------------


def test_max_drawdown_non_positive() -> None:
    """最大DD は 0 以下（ドローダウンは損失方向）。"""
    prices = [100.0, 110.0, 90.0, 80.0, 100.0]
    bench = [1000.0, 1010.0, 990.0, 980.0, 1000.0]
    panel, benchmark = _panel_and_bench({"A": prices}, bench)
    result = backtest_portfolio(panel, {"A": 1.0}, benchmark)
    assert result["portfolio"]["max_drawdown"] <= 0.0


# ---------------------------------------------------------------------------
# is_delayed は常に True（ADR-008）
# ---------------------------------------------------------------------------


def test_is_delayed_always_true() -> None:
    """is_delayed は Free 12週遅延のため常に True（ADR-008）。"""
    prices = list(np.linspace(100.0, 110.0, 20))
    bench = list(np.linspace(1000.0, 1050.0, 20))
    panel, benchmark = _panel_and_bench({"A": prices}, bench)
    result = backtest_portfolio(panel, {"A": 1.0}, benchmark)
    assert result["is_delayed"] is True


# ---------------------------------------------------------------------------
# 空パネル → エラーなく空結果
# ---------------------------------------------------------------------------


def test_empty_panel_returns_empty_result() -> None:
    """空のパネルを渡してもエラーにならず空結果が返る。"""
    panel = pd.DataFrame()
    benchmark = pd.Series(dtype=float)
    result = backtest_portfolio(panel, {}, benchmark)
    assert result["portfolio"]["curve"] == []
    assert result["benchmark"]["curve"] == []
    assert result["excess_return"] == 0.0


# ---------------------------------------------------------------------------
# rebalance='monthly' は 'none' と同じ挙動（Phase 2 初期未実装・注記確認）
# ---------------------------------------------------------------------------


def test_rebalance_monthly_behaves_same_as_none() -> None:
    """rebalance='monthly' は現状 'none' と同じ累積リターンを返す（未実装）。"""
    prices = list(np.linspace(100.0, 120.0, 50))
    bench = list(np.linspace(1000.0, 1100.0, 50))
    panel, benchmark = _panel_and_bench({"A": prices}, bench)

    result_none = backtest_portfolio(panel, {"A": 1.0}, benchmark, rebalance="none")
    result_monthly = backtest_portfolio(panel, {"A": 1.0}, benchmark, rebalance="monthly")

    assert (
        abs(
            result_none["portfolio"]["cumulative_return"]
            - result_monthly["portfolio"]["cumulative_return"]
        )
        < 1e-9
    )
