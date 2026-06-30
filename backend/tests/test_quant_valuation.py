"""quant.valuation の純関数テスト（ADR-014/016・ADR-031）。

DB に触れずスカラ直書きで検証する（実 API も叩かない＝testing-strategy）。
"""

from __future__ import annotations

from app.quant.valuation import (
    compute_valuation,
    dividend_yield,
    forecast_achievement,
    forecast_guidance,
    forecast_revision,
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


# --- 会社予想（ガイダンス・ADR-063 #4） ---


def test_forecast_achievement_basic_and_guards() -> None:
    # 実績/予想（1.0=予想線・>1 beat・<1 miss）
    assert forecast_achievement(105.0, 100.0) == 1.05
    assert forecast_achievement(90.0, 100.0) == 0.9
    # 予想黒字なのに赤字転落は負の達成率＝事実として返す（severe miss）
    assert forecast_achievement(-20.0, 100.0) == -0.2
    # 予想が None・0 以下は比が壊れるため None（捏造しない）
    assert forecast_achievement(105.0, None) is None
    assert forecast_achievement(105.0, 0.0) is None
    assert forecast_achievement(105.0, -50.0) is None
    assert forecast_achievement(None, 100.0) is None


def test_forecast_revision_basic_and_guards() -> None:
    # 新予想/旧予想 - 1（+ 上方・- 下方）
    assert abs(forecast_revision(380.0, 340.0) - (380.0 / 340.0 - 1)) < 1e-12
    assert abs(forecast_revision(90.0, 100.0) - (-0.1)) < 1e-12
    assert forecast_revision(50.0, 0.0) is None
    assert forecast_revision(50.0, -10.0) is None
    assert forecast_revision(None, 100.0) is None
    assert forecast_revision(100.0, None) is None


def _row(date: str, period: str, op=None, profit=None, f_op=None, f_profit=None) -> dict:
    return {
        "disclosed_date": date,
        "fiscal_period": period,
        "operating_profit": op,
        "profit": profit,
        "forecast_operating_profit": f_op,
        "forecast_profit": f_profit,
    }


def test_forecast_guidance_beat_and_upward_revision_like_7203() -> None:
    # 実機 7203（営業利益・百万円単位を兆で簡略）の並びを再現:
    #   FY2025 の最終 standing 予想は 3Q(2025-02) の 4.70、FY実績(2025-05) が 4.80 → beat。
    #   進行中FY2026 の予想は 1Q 3.20 → 2Q 3.40 → 3Q 3.80（上方修正）。
    rows = [
        _row("2024-05-08", "FY", op=5.35),  # 前々期FY実績
        _row("2024-08-01", "1Q", f_op=4.30),  # FY2025 予想
        _row("2024-11-06", "2Q", f_op=4.30),
        _row("2025-02-05", "3Q", f_op=4.70),  # 上方修正（最終 standing）
        _row("2025-05-08", "FY", op=4.80),  # FY2025 実績 → 4.80/4.70 ≈ beat
        _row("2025-08-07", "1Q", f_op=3.20),  # FY2026 予想
        _row("2025-11-05", "2Q", f_op=3.40),
        _row("2026-02-06", "3Q", f_op=3.80),  # 直近 上方修正
    ]
    g = forecast_guidance(rows)
    assert abs(g["op_forecast_achievement"] - 4.80 / 4.70) < 1e-12  # beat
    assert abs(g["op_forecast_revision"] - (3.80 / 3.40 - 1)) < 1e-12  # 上方修正
    # 純利益は素が無いので None（営業利益と独立）
    assert g["profit_forecast_achievement"] is None
    assert g["profit_forecast_revision"] is None


def test_forecast_guidance_no_forecast_company_is_all_none() -> None:
    # 予想を出さない会社（実機 9984＝FOP/FNP 全空）→ 全 None（捏造しない）
    rows = [
        _row("2025-05-13", "FY", op=None, profit=1.15),
        _row("2025-08-07", "1Q", profit=0.42),
        _row("2026-02-12", "3Q", profit=3.17),
    ]
    g = forecast_guidance(rows)
    assert all(v is None for v in g.values())


def test_forecast_guidance_revision_needs_two_in_progress_disclosures() -> None:
    # 進行中FY の予想開示が 1 件だけ → 修正は None（achievement は出る）
    rows = [
        _row("2024-02-05", "3Q", f_op=4.70),  # 前FY 最終予想
        _row("2024-05-08", "FY", op=4.80),  # 前FY 実績 → beat
        _row("2024-08-07", "1Q", f_op=3.20),  # 進行中FY 予想は 1 件のみ
    ]
    g = forecast_guidance(rows)
    assert abs(g["op_forecast_achievement"] - 4.80 / 4.70) < 1e-12
    assert g["op_forecast_revision"] is None


def test_forecast_guidance_no_fy_row_uses_all_forecast_rows_for_revision() -> None:
    # FY実績行が無い（新規上場直後等）→ achievement None・修正は全予想行の直近 2 件
    rows = [
        _row("2025-08-07", "1Q", f_op=3.20),
        _row("2025-11-05", "2Q", f_op=3.40),
    ]
    g = forecast_guidance(rows)
    assert g["op_forecast_achievement"] is None
    assert abs(g["op_forecast_revision"] - (3.40 / 3.20 - 1)) < 1e-12


def test_forecast_guidance_empty_rows() -> None:
    g = forecast_guidance([])
    assert g == {
        "op_forecast_achievement": None,
        "profit_forecast_achievement": None,
        "op_forecast_revision": None,
        "profit_forecast_revision": None,
    }
