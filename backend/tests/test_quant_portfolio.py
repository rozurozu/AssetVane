"""quant.portfolio の既知系列テスト（ADR-016: テスト済みコードで実装）。

設計の真実: docs/phase-specs/phase2-spec.md §4.1・§4.2・§8。
DB に触れず手組み DataFrame で純関数を検証する（実 API も叩かない）。
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from app.quant.portfolio import RISK_FREE_RATE, compute_deviations, compute_portfolio_metrics

# ---------------------------------------------------------------------------
# ヘルパ
# ---------------------------------------------------------------------------


def _panel(prices: dict[str, list[float]], base_date: str = "2024-01-01") -> pd.DataFrame:
    """code -> adj_close リストから date-index の price_panel を作る。"""
    n = max(len(v) for v in prices.values())
    dates = pd.date_range(base_date, periods=n, freq="B")
    return pd.DataFrame(prices, index=dates)


def _uniform_panel(n: int, codes: list[str], start: float = 100.0) -> pd.DataFrame:
    """全銘柄が同じ等差数列（リターン一定）のパネルを作る。"""
    arr = np.linspace(start, start * 1.5, n)
    return _panel({c: list(arr) for c in codes})


# ---------------------------------------------------------------------------
# シャープの既知値テスト
# ---------------------------------------------------------------------------


def test_sharpe_known_value() -> None:
    """既知の固定日次リターン系列でシャープが手計算値と ±0.01 一致する。

    2 資産・等ウェイト。実装は直近 252 営業日にルックバックするため、
    手計算も同じ直近 252 行を使って検証する。
    """
    np.random.seed(42)
    # ルックバック=252 とちょうど一致する長さにして切り取りを発生させない
    n = 252
    # 毎日 +0.1%・+0.2% のリターンに微小ノイズを加えた adj_close を生成
    r1 = 0.001 + np.random.normal(0, 0.005, n)
    r2 = 0.002 + np.random.normal(0, 0.005, n)
    p1 = np.cumprod(1.0 + r1) * 100.0
    p2 = np.cumprod(1.0 + r2) * 100.0
    panel = _panel({"A": list(p1), "B": list(p2)})
    weights = {"A": 0.5, "B": 0.5}
    result = compute_portfolio_metrics(panel, weights)

    # 手計算（実装と同じ直近 252 行・pct_change().dropna()）
    ret_a = pd.Series(p1).pct_change().dropna()
    ret_b = pd.Series(p2).pct_change().dropna()
    port_r = 0.5 * ret_a.to_numpy(dtype=float) + 0.5 * ret_b.to_numpy(dtype=float)
    expected_sharpe = (port_r.mean() * 252 - RISK_FREE_RATE) / (port_r.std(ddof=1) * math.sqrt(252))

    assert result["sharpe"] is not None
    assert abs(result["sharpe"] - expected_sharpe) < 0.01


# ---------------------------------------------------------------------------
# 最大ドローダウンの既知値テスト
# ---------------------------------------------------------------------------


def test_max_drawdown_known_value() -> None:
    """山→谷→山の adj_close 系列で最大DD が既知値と一致する。

    実装は pct_change().dropna() で先頭行を捨てるため、
    prices[1]=95 が累積起点となる。
    累積リターン系列（prices[1] 起点）:
      95 → 85 → 70 → 80 → 95 → 110
    対応する累積値（1 始まり）:
      1.0000, 0.8947, 0.7368, 0.8421, 1.0000, 1.1579
    最大DD = 70/95 - 1 = -0.26315...
    """
    prices = [100.0, 95.0, 85.0, 70.0, 80.0, 95.0, 110.0]
    panel = _panel({"X": prices})
    result = compute_portfolio_metrics(panel, {"X": 1.0})

    assert result["max_drawdown"] is not None
    # pct_change().dropna() で prices[0] が捨てられるため、
    # prices[1]=95 が累積起点 → MDD = 70/95 - 1 = -0.2632
    expected_mdd = 70.0 / 95.0 - 1.0
    assert abs(result["max_drawdown"] - expected_mdd) < 0.001


# ---------------------------------------------------------------------------
# deviations: 判定方向テスト
# ---------------------------------------------------------------------------


def test_deviation_position_breached_when_over_limit() -> None:
    """max_position_weight 超過 → breached=True。"""
    weights = {"A": 0.25, "B": 0.20, "C": 0.15}
    policy = {"max_position_weight": 0.20}
    devs = compute_deviations(weights, 0.1, {}, policy)
    pos_dev = next(d for d in devs if d["kind"] == "max_position")
    assert pos_dev["breached"] is True  # A=0.25 > 0.20
    assert abs(pos_dev["current"] - 0.25) < 1e-9


def test_deviation_position_not_breached_when_at_limit() -> None:
    """max_position_weight ちょうど → breached=False（超過は厳密に >）。"""
    weights = {"A": 0.20, "B": 0.15}
    policy = {"max_position_weight": 0.20}
    devs = compute_deviations(weights, 0.1, {}, policy)
    pos_dev = next(d for d in devs if d["kind"] == "max_position")
    assert pos_dev["breached"] is False


def test_deviation_cash_breached_when_below_target() -> None:
    """現金比率が target_cash_ratio を**下回る**と breached=True（下回りで違反）。"""
    policy = {"target_cash_ratio": 0.10}
    devs = compute_deviations({}, 0.05, {}, policy)
    cash_dev = next(d for d in devs if d["kind"] == "cash_ratio")
    assert cash_dev["breached"] is True  # 0.05 < 0.10


def test_deviation_cash_not_breached_when_above_target() -> None:
    """現金比率が target_cash_ratio 以上 → breached=False。"""
    policy = {"target_cash_ratio": 0.10}
    devs = compute_deviations({}, 0.15, {}, policy)
    cash_dev = next(d for d in devs if d["kind"] == "cash_ratio")
    assert cash_dev["breached"] is False  # 0.15 >= 0.10


def test_deviation_sector_breached_when_over_cap() -> None:
    """業種合計ウェイトが sector_caps を超過 → breached=True。"""
    policy = {"sector_caps": {"電気機器": 0.30}}
    sector_weights = {"電気機器": 0.35}
    devs = compute_deviations({}, 0.1, sector_weights, policy)
    sec_dev = next(d for d in devs if d["kind"] == "sector_cap")
    assert sec_dev["breached"] is True


def test_deviation_sector_not_breached_when_under_cap() -> None:
    """業種合計ウェイトが cap 以下 → breached=False。"""
    policy = {"sector_caps": {"電気機器": 0.30}}
    sector_weights = {"電気機器": 0.25}
    devs = compute_deviations({}, 0.1, sector_weights, policy)
    sec_dev = next(d for d in devs if d["kind"] == "sector_cap")
    assert sec_dev["breached"] is False


# ---------------------------------------------------------------------------
# adj_close null → skip
# ---------------------------------------------------------------------------


def test_null_adj_close_skips_column() -> None:
    """窓内に adj_close null がある銘柄は skip される（補間しない＝裁定 L-26）。"""
    p_good = list(np.linspace(100, 150, 20))
    p_bad = list(np.linspace(100, 150, 20))
    p_bad[10] = float("nan")  # 途中に null
    panel = _panel({"GOOD": p_good, "BAD": p_bad})
    result = compute_portfolio_metrics(panel, {"GOOD": 0.5, "BAD": 0.5})
    # BAD は除外されるので correlation には GOOD のみ（1 銘柄→相関計算不可→空）
    assert "BAD" not in result["correlation"]["codes"]


def test_all_null_returns_none_metrics() -> None:
    """全銘柄が null → 指標はすべて None・correlation は空。"""
    p = [float("nan")] * 20
    panel = _panel({"A": p})
    result = compute_portfolio_metrics(panel, {"A": 1.0})
    assert result["annual_return"] is None
    assert result["sharpe"] is None
    assert result["correlation"]["codes"] == []


# ---------------------------------------------------------------------------
# 1 銘柄 → 相関は空
# ---------------------------------------------------------------------------


def test_single_stock_correlation_empty() -> None:
    """1 銘柄の場合、correlation は空配列（相関は 2 銘柄以上で定義）。"""
    panel = _uniform_panel(50, ["A"])
    result = compute_portfolio_metrics(panel, {"A": 1.0})
    assert result["correlation"]["codes"] == []
    assert result["correlation"]["matrix"] == []


# ---------------------------------------------------------------------------
# policy=None → deviations 空
# ---------------------------------------------------------------------------


def test_no_policy_returns_empty_deviations() -> None:
    """policy=None なら deviations は空配列。"""
    panel = _uniform_panel(50, ["A", "B"])
    result = compute_portfolio_metrics(panel, {"A": 0.5, "B": 0.5}, policy=None)
    assert result["deviations"] == []


# ---------------------------------------------------------------------------
# is_delayed は返さない（ADR-071: 鮮度は as_of から呼び出し側が判定・quant は today を知らない）
# ---------------------------------------------------------------------------


def test_no_is_delayed_key() -> None:
    """quant は is_delayed を返さない（ADR-071・ADR-016）。as_of だけ返す。"""
    panel = _uniform_panel(30, ["A", "B"])
    result = compute_portfolio_metrics(panel, {"A": 0.5, "B": 0.5})
    assert "is_delayed" not in result
    assert "as_of" in result


# ---------------------------------------------------------------------------
# lookback_days: 実際に使った日数が返る
# ---------------------------------------------------------------------------


def test_lookback_days_returned() -> None:
    """lookback_days に計算に使った日数（日次リターン行数）が入る。"""
    n = 30
    panel = _uniform_panel(n, ["A", "B"])
    result = compute_portfolio_metrics(panel, {"A": 0.5, "B": 0.5})
    assert result["lookback_days"] is not None
    # pct_change().dropna() で先頭1行が落ちるので n-1
    assert result["lookback_days"] == n - 1


# ---------------------------------------------------------------------------
# correlation matrix の対角は 1.0
# ---------------------------------------------------------------------------


def test_correlation_diagonal_is_one() -> None:
    """相関行列の対角成分は 1.0（自己相関）。"""
    np.random.seed(0)
    n = 50
    prices = {
        "A": list(np.cumprod(1.0 + np.random.normal(0, 0.01, n)) * 100),
        "B": list(np.cumprod(1.0 + np.random.normal(0, 0.01, n)) * 100),
    }
    panel = _panel(prices)
    result = compute_portfolio_metrics(panel, {"A": 0.5, "B": 0.5})
    matrix = result["correlation"]["matrix"]
    assert len(matrix) == 2
    assert abs(matrix[0][0] - 1.0) < 1e-9
    assert abs(matrix[1][1] - 1.0) < 1e-9


# ---------------------------------------------------------------------------
# compute_deviations: labels が使われる
# ---------------------------------------------------------------------------


def test_deviation_label_uses_company_name() -> None:
    """labels dict がある場合、max_position の label に銘柄名が使われる。"""
    weights = {"7203": 0.25}
    policy = {"max_position_weight": 0.20}
    labels = {"7203": "トヨタ自動車"}
    devs = compute_deviations(weights, 0.1, {}, policy, labels=labels)
    pos_dev = next(d for d in devs if d["kind"] == "max_position")
    assert pos_dev["label"] == "トヨタ自動車"


# ---------------------------------------------------------------------------
# compute_deviations: 空の weights で position 逸脱なし
# ---------------------------------------------------------------------------


def test_deviation_empty_weights_no_position_entry() -> None:
    """weights が空なら max_position 逸脱エントリは生成されない。"""
    policy = {"max_position_weight": 0.20}
    devs = compute_deviations({}, 0.1, {}, policy)
    assert not any(d["kind"] == "max_position" for d in devs)
