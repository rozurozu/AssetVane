"""features.build_features_at の検証 — point-in-time 特徴量・同period突合・リーク防止・skip。

担保（phase5-spec.md §2・ADR-014/016）:
- 既知 financials/価格から期待 YoY/PER/PBR/モメンタムが出る。
- YoY は同一 fiscal_period タイプの直前行（＝前年同期）と突合する（FY を誤選択しない）。
- as_of を越える未来情報（未開示の財務・未来の株価）が結果に混ざらない（リーク防止）。
- アンカー株価不在・ファンダ全 NaN は None（skip・補間しない）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.quant.ml.features import FEATURE_NAMES, build_features_at


def _prices(adj: list[float], *, end: str) -> pd.DataFrame:
    """end を最終日とする日次（暦日）株価 DataFrame（date 昇順・adj_close=adj）。"""
    dates = pd.date_range(end=end, periods=len(adj), freq="D").strftime("%Y-%m-%d")
    return pd.DataFrame({"date": list(dates), "adj_close": adj})


def _fin(
    disclosed_date: str,
    fiscal_period: str,
    *,
    net_sales: float | None = None,
    operating_profit: float | None = None,
    profit: float | None = None,
    eps: float | None = None,
    bps: float | None = None,
) -> dict:
    return {
        "disclosed_date": disclosed_date,
        "fiscal_period": fiscal_period,
        "net_sales": net_sales,
        "operating_profit": operating_profit,
        "profit": profit,
        "eps": eps,
        "bps": bps,
    }


def test_known_inputs_expected_features() -> None:
    """FY 2 期＋価格 → 売上/営業利益/純利益/EPS の YoY・営業利益率・PER/PBR・モメンタムが期待値。"""
    adj = list(np.linspace(1000.0, 1200.0, 65))  # 65 営業日・最終 1200
    prices = _prices(adj, end="2025-06-01")
    fin = [
        _fin("2024-05-10", "FY", net_sales=1000, operating_profit=100, profit=80, eps=50, bps=500),
        _fin("2025-05-10", "FY", net_sales=1200, operating_profit=150, profit=96, eps=60, bps=550),
    ]

    feats = build_features_at(fin, prices, as_of="2025-06-01")
    assert feats is not None
    assert set(feats.keys()) == set(FEATURE_NAMES)

    assert feats["sales_growth_yoy"] == 1200 / 1000 - 1  # 0.2
    assert feats["operating_profit_growth_yoy"] == 150 / 100 - 1  # 0.5
    assert feats["profit_growth_yoy"] == 96 / 80 - 1  # 0.2
    assert feats["operating_margin"] == 150 / 1200  # 0.125
    assert feats["eps_growth_yoy"] == 60 / 50 - 1  # 0.2
    assert feats["per"] == 1200.0 / 60  # 20.0
    assert feats["pbr"] == 1200.0 / 550
    # モメンタムは -1 と -61 の窓を取る（adj[-1]/adj[-61]-1）。
    assert feats["momentum_3m"] == adj[-1] / adj[-1 - 60] - 1
    # 開示日 2025-05-10 が価格窓内なのでサプライズ代理は有限値。
    assert np.isfinite(feats["surprise_proxy"])


def test_surprise_proxy_consistent_train_vs_serve() -> None:
    """#6: 同じ (code,開示) の surprise_proxy が as_of=開示日(学習)・as_of=後日(推論) で一致する。

    旧実装は post=min(idx+window, as_of境界) が as_of 依存で、学習は開示前窓・推論は開示後込み窓に
    なり train/serve skew を生んでいた。開示前窓に統一して as_of 非依存にしたことを固定する。
    """
    adj = list(np.linspace(1000.0, 1300.0, 120))  # 120 営業日（暦日）
    prices = _prices(adj, end="2025-06-01")  # 2025-02-02..2025-06-01
    disclosed = "2025-04-15"  # 価格窓の中ほど
    fin = [_fin(disclosed, "FY", net_sales=1000, operating_profit=100, profit=80, eps=50, bps=500)]

    train = build_features_at(fin, prices, as_of=disclosed)  # 価格が開示日で切れる（学習）
    serve = build_features_at(fin, prices, as_of="2025-06-01")  # 開示後の株価も既知（推論）
    assert train is not None and serve is not None
    assert np.isfinite(train["surprise_proxy"])
    # as_of に依らず一致（開示後の反応を含めない＝skew なし）。
    assert train["surprise_proxy"] == serve["surprise_proxy"]


def test_yoy_matches_same_period_type_not_fy() -> None:
    """latest が 2Q なら前年 2Q と突合する（FY を誤って使わない）。"""
    prices = _prices([1000.0] * 5, end="2025-09-01")
    fin = [
        _fin("2024-05-10", "FY", net_sales=1000, eps=50, bps=500),
        _fin("2024-08-01", "2Q", net_sales=500, operating_profit=50, profit=40),
        _fin("2025-08-01", "2Q", net_sales=600, operating_profit=66, profit=48),
    ]
    feats = build_features_at(fin, prices, as_of="2025-09-01")
    assert feats is not None
    # 600/500-1=0.2（前年 2Q）。FY(1000)を使うと -0.4 になるので識別できる。
    assert feats["sales_growth_yoy"] == 600 / 500 - 1
    # FY は 2024 の 1 本だけ → 前 FY 無し → EPS 成長率は NaN。
    assert np.isnan(feats["eps_growth_yoy"])
    # PER は最新FY(2024)の eps=50 を使う。
    assert feats["per"] == 1000.0 / 50


def test_no_future_financial_leak() -> None:
    """as_of が最新 FY 開示前なら、その未開示行を使わず前期 FY で計算する（リーク防止）。"""
    prices = _prices([1000.0] * 5, end="2025-04-01")
    fin = [
        _fin("2024-05-10", "FY", net_sales=1000, eps=50, bps=500),
        _fin("2025-05-10", "FY", net_sales=1200, eps=60, bps=550),  # as_of 後＝未開示
    ]
    feats = build_features_at(fin, prices, as_of="2025-04-01")
    assert feats is not None
    # 未開示の eps=60 を使わず eps=50 で PER を出す。
    assert feats["per"] == 1000.0 / 50


def test_no_future_price_leak() -> None:
    """as_of より後の株価（巨大スパイク）が PER・モメンタムに混ざらない。"""
    fin = [_fin("2024-05-10", "FY", net_sales=1000, eps=50, bps=500)]
    base = _prices([1000.0] * 65, end="2025-06-01")
    spiked = pd.concat(
        [base, pd.DataFrame({"date": ["2025-12-31"], "adj_close": [99999.0]})],
        ignore_index=True,
    )
    f_base = build_features_at(fin, base, as_of="2025-06-01")
    f_spiked = build_features_at(fin, spiked, as_of="2025-06-01")
    assert f_base is not None and f_spiked is not None
    # 未来のスパイクは as_of で切られるので PER は不変（cur_price=1000）。
    assert f_spiked["per"] == f_base["per"] == 1000.0 / 50


def test_skip_when_no_financials_known() -> None:
    """as_of までに開示済み財務が無ければ None。"""
    prices = _prices([1000.0] * 5, end="2025-06-01")
    fin = [_fin("2025-12-01", "FY", net_sales=1000, eps=50, bps=500)]  # 全て as_of 後
    assert build_features_at(fin, prices, as_of="2025-06-01") is None


def test_skip_when_no_price() -> None:
    """アンカー株価が無ければ None。"""
    fin = [_fin("2024-05-10", "FY", net_sales=1000, eps=50, bps=500)]
    empty = pd.DataFrame({"date": [], "adj_close": []})
    assert build_features_at(fin, empty, as_of="2025-06-01") is None


def test_skip_when_all_fundamentals_nan() -> None:
    """前期無し・赤字・売上0 でファンダが全 NaN なら None（捏造しない）。"""
    prices = _prices([1000.0] * 5, end="2025-06-01")
    fin = [
        _fin("2024-05-10", "FY", net_sales=0, operating_profit=None, profit=None, eps=-5, bps=-10)
    ]
    assert build_features_at(fin, prices, as_of="2025-06-01") is None
