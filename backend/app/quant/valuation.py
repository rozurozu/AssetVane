"""バリュエーション・ファンダ指標の純関数（ADR-014/016・ADR-031・ADR-048）。

設計の真実: docs/decisions.md ADR-031（スクリーナー設計）・ADR-048（ROE/利益率/成長率の追加）。

- **純関数・DB 非依存**（ADR-016）。引数はスカラ、戻り値もスカラ or None。`repo` は触らない。
- **数字を作らない**（ADR-014）。分母が欠損・0 以下など計算が無意味なら None を返す（捏造しない）。
- PER/PBR は「直近通期(FY)実績 EPS/BPS」を、配当利回りは「予想年間配当」を、時価総額は
  「(発行済 − 自己株) × 終値」を使う前提（採用行の選定は services 層・ADR-031 の方針）。
- 損益がマイナス（eps<=0 / bps<=0）の PER/PBR は慣習上意味を成さないため None にする。
- ROE/利益率/成長率（ADR-048）も同じ規律。ROE/利益率は分子がマイナスでも事実として意味を持つ
  ため返す（分母の bps/sales が 0 以下のときだけ None）。成長率 YoY は前年が 0 以下なら意味を
  成さないため None にする（マイナス基準からの増減率は誤解を招く）。
"""

from __future__ import annotations

from typing import Any


def per(close: float | None, eps: float | None) -> float | None:
    """PER = 終値 / EPS。eps が None・0 以下なら None（赤字や欠損は捏造しない）。"""
    if close is None or eps is None or eps <= 0:
        return None
    return close / eps


def pbr(close: float | None, bps: float | None) -> float | None:
    """PBR = 終値 / BPS。bps が None・0 以下なら None。"""
    if close is None or bps is None or bps <= 0:
        return None
    return close / bps


def market_cap(close: float | None, shares_net: float | None) -> float | None:
    """時価総額 = 終値 × (発行済株式数 − 自己株式)。どちらか欠損・株数<=0 なら None。"""
    if close is None or shares_net is None or shares_net <= 0:
        return None
    return close * shares_net


def dividend_yield(dividend_per_share: float | None, close: float | None) -> float | None:
    """配当利回り = 年間配当 / 終値（0..1）。close が None・0 以下なら None。

    無配（配当 0）は欠損ではなく事実なので 0.0 を返す（None にしない）。
    """
    if close is None or close <= 0 or dividend_per_share is None:
        return None
    return dividend_per_share / close


def roe(eps: float | None, bps: float | None) -> float | None:
    """ROE = EPS / BPS（= 純利益/自己資本・0..1）。bps が None・0 以下なら None（ADR-048）。

    赤字（eps<0）は負の ROE として事実なので返す（PER と違いマイナスでも意味を持つ）。
    """
    if eps is None or bps is None or bps <= 0:
        return None
    return eps / bps


def operating_margin(operating_profit: float | None, net_sales: float | None) -> float | None:
    """営業利益率 = 営業利益 / 売上高（0..1）。売上が None・0 以下なら None（ADR-048）。

    営業赤字は負の利益率として事実なので返す。
    """
    if operating_profit is None or net_sales is None or net_sales <= 0:
        return None
    return operating_profit / net_sales


def net_margin(profit: float | None, net_sales: float | None) -> float | None:
    """純利益率 = 純利益 / 売上高（0..1）。売上が None・0 以下なら None（ADR-048）。"""
    if profit is None or net_sales is None or net_sales <= 0:
        return None
    return profit / net_sales


def growth_yoy(curr: float | None, prev: float | None) -> float | None:
    """前年比成長率 = curr / prev − 1（0..1 基準の比率）。前年が None・0 以下なら None（ADR-048）。

    マイナス基準（赤字→黒字 等）からの増減率は誤解を招くため、prev<=0 は計算せず None にする。
    突合する curr/prev は同一 fiscal_period タイプの直前 FY 行（採用は services 層・ADR-048）。
    """
    if curr is None or prev is None or prev <= 0:
        return None
    return curr / prev - 1.0


def compute_valuation(
    close: float | None,
    eps: float | None,
    bps: float | None,
    dividend_per_share: float | None,
    shares_net: float | None,
) -> dict[str, Any]:
    """採用済みの素データ（終値・EPS・BPS・年間配当・純発行株数）から 4 指標を組む純関数。

    どの財務行を採用するか（最新FY行の eps/bps、最新行の配当/株数）は services 層が決め、
    ここはスカラを受けて計算するだけ（ADR-031）。各値は計算不能なら None。
    """
    return {
        "per": per(close, eps),
        "pbr": pbr(close, bps),
        "market_cap": market_cap(close, shares_net),
        "dividend_yield": dividend_yield(dividend_per_share, close),
    }
