"""quant.valuation の純関数テスト（ADR-014/016・ADR-031）。

DB に触れずスカラ直書きで検証する（実 API も叩かない＝testing-strategy）。
"""

from __future__ import annotations

from app.quant.valuation import (
    compute_valuation,
    dividend_yield,
    growth_yoy,
    market_cap,
    net_margin,
    operating_margin,
    pbr,
    per,
    roe,
)


def test_per_basic() -> None:
    assert per(2000.0, 100.0) == 20.0


def test_per_none_when_eps_missing_or_nonpositive() -> None:
    # 欠損・赤字（eps<=0）は捏造せず None（ADR-014）
    assert per(2000.0, None) is None
    assert per(2000.0, 0.0) is None
    assert per(2000.0, -50.0) is None
    assert per(None, 100.0) is None


def test_pbr_basic_and_guards() -> None:
    assert pbr(2753.09, 2753.09) == 1.0
    assert pbr(2000.0, None) is None
    assert pbr(2000.0, 0.0) is None
    assert pbr(2000.0, -10.0) is None


def test_market_cap_net_of_treasury() -> None:
    # (発行済 - 自己株) × 終値
    shares_net = 15_794_987_460 - 2_761_600_733
    assert market_cap(2800.0, shares_net) == 2800.0 * shares_net
    assert market_cap(2800.0, None) is None
    assert market_cap(2800.0, 0.0) is None
    assert market_cap(None, shares_net) is None


def test_dividend_yield_basic() -> None:
    # 95 円配当 / 2800 円 ≈ 3.39%
    y = dividend_yield(95.0, 2800.0)
    assert y is not None
    assert abs(y - 95.0 / 2800.0) < 1e-12


def test_dividend_yield_zero_is_fact_not_missing() -> None:
    # 無配は欠損ではなく事実 → 0.0（None にしない）
    assert dividend_yield(0.0, 2800.0) == 0.0
    # 配当欠損・終値<=0 は None
    assert dividend_yield(None, 2800.0) is None
    assert dividend_yield(95.0, 0.0) is None


def test_compute_valuation_bundle() -> None:
    out = compute_valuation(
        close=2000.0, eps=100.0, bps=1000.0, dividend_per_share=40.0, shares_net=1_000_000
    )
    assert out == {
        "per": 20.0,
        "pbr": 2.0,
        "market_cap": 2000.0 * 1_000_000,
        "dividend_yield": 40.0 / 2000.0,
    }


def test_compute_valuation_partial_missing() -> None:
    # bps 欠損（四半期で BPS が空のケース）→ pbr のみ None、他は出る
    out = compute_valuation(
        close=2000.0, eps=100.0, bps=None, dividend_per_share=None, shares_net=None
    )
    assert out["per"] == 20.0
    assert out["pbr"] is None
    assert out["market_cap"] is None
    assert out["dividend_yield"] is None


# --- ファンダ指標（ADR-048） ---


def test_roe_basic_and_guards() -> None:
    # ROE = EPS / BPS。赤字（eps<0）は負の ROE として事実なので返す（PER と違う）
    assert roe(100.0, 1000.0) == 0.1
    assert roe(-50.0, 1000.0) == -0.05
    assert roe(100.0, None) is None
    assert roe(None, 1000.0) is None
    assert roe(100.0, 0.0) is None
    assert roe(100.0, -10.0) is None


def test_margins_basic_and_guards() -> None:
    assert operating_margin(20.0, 100.0) == 0.2
    assert net_margin(8.0, 100.0) == 0.08
    # 赤字は負の利益率として事実
    assert operating_margin(-5.0, 100.0) == -0.05
    # 売上 0 以下・欠損は None（捏造しない）
    assert operating_margin(20.0, 0.0) is None
    assert net_margin(8.0, None) is None
    assert operating_margin(None, 100.0) is None


def test_growth_yoy_basic_and_guards() -> None:
    assert abs(growth_yoy(110.0, 100.0) - 0.1) < 1e-12
    assert abs(growth_yoy(90.0, 100.0) - (-0.1)) < 1e-12
    # 前年が 0 以下（赤字→黒字 等）は意味を成さず None
    assert growth_yoy(50.0, 0.0) is None
    assert growth_yoy(50.0, -10.0) is None
    assert growth_yoy(None, 100.0) is None
    assert growth_yoy(100.0, None) is None
