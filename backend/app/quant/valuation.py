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


# --- 会社予想（ガイダンス）の質シグナル（ADR-063 #4） ---


def forecast_achievement(actual: float | None, forecast: float | None) -> float | None:
    """会社予想に対する達成率 = 実績 / 予想（1.0=予想線・>1 で beat・<1 で miss）。

    予想が None・0 以下なら None（赤字予想は比で符号が壊れ解釈不能ゆえ計算しない＝growth_yoy
    と同方針）。実績がマイナス（予想黒字なのに赤字転落）は負の達成率として事実なので返す（ROE と
    同じ規律）。良し悪し（beat/miss の評価）は LLM が解釈する（ADR-014）。
    """
    if actual is None or forecast is None or forecast <= 0:
        return None
    return actual / forecast


def forecast_revision(curr: float | None, prev: float | None) -> float | None:
    """会社予想の修正率 = 新予想 / 旧予想 − 1（+ で上方修正・− で下方修正）。

    旧予想が None・0 以下なら None（growth_yoy 同方針）。突合する curr/prev は同一会計年度内の
    連続開示の予想（採用は services 層）。上方/下方の善し悪しは LLM が解釈する（ADR-014）。
    """
    if curr is None or prev is None or prev <= 0:
        return None
    return curr / prev - 1.0


def _is_fy_period(period: str | None) -> bool:
    """会計期間種別が通期(FY)か（'FY' / 'FY2025' 等・実機は 'FY'）。"""
    return (period or "").upper().startswith("FY")


def forecast_guidance(rows: list[dict[str, Any]]) -> dict[str, float | None]:
    """銘柄の財務開示列（実績＋会社予想）から beat/miss と上方/下方修正を組む純関数（ADR-063 #4）。

    DB 非依存（既に取得済みの素の dict 列を受けるだけ・採用は services 層が rows を渡す）。各 row は
    disclosed_date / fiscal_period / operating_profit / profit / forecast_operating_profit /
    forecast_profit を持つ前提（欠損は None）。実機の形（実機確認 2026-06-30）:
      - 当期FY予想 forecast_* は各四半期(1Q/2Q/3Q)開示に standing で載り、FY実績行では空(None)。
      - FY実績行（fiscal_period が FY）に当期の実績 operating_profit/profit が載る。
    これを使い 2 つの事実を出す（評価は LLM・ADR-014）:
      - achievement（beat/miss・後ろ向き）= 最新完了FY 実績 ÷ その期の最終 standing 予想
        （= FY実績行の開示直前にあった予想）。
      - revision（上方/下方修正・前向き）= 進行中FY（最新FY開示より後）の予想の直近 2 開示の差。
    予想を出さない会社（forecast_* 全 None）や素が足りない場合は各値 None（捏造しない）。
    """
    out: dict[str, float | None] = {
        "op_forecast_achievement": None,
        "profit_forecast_achievement": None,
        "op_forecast_revision": None,
        "profit_forecast_revision": None,
    }
    if not rows:
        return out

    def _date(r: dict[str, Any]) -> str:
        return str(r.get("disclosed_date") or "")

    srt = sorted(rows, key=_date)
    fy_rows = [r for r in srt if _is_fy_period(r.get("fiscal_period"))]
    latest_fy = fy_rows[-1] if fy_rows else None
    fy_date = _date(latest_fy) if latest_fy is not None else ""  # 比較は常に str 同士

    # (実績列, 予想列, 達成率キー, 修正キー) を営業利益・純利益で回す
    specs = (
        (
            "operating_profit",
            "forecast_operating_profit",
            "op_forecast_achievement",
            "op_forecast_revision",
        ),
        ("profit", "forecast_profit", "profit_forecast_achievement", "profit_forecast_revision"),
    )
    for actual_key, fc_key, ach_key, rev_key in specs:
        # beat/miss: 最新完了FY 実績 ÷ その FY 開示直前の最終 standing 予想（前年の最終Q予想）
        if latest_fy is not None:
            standing = None
            for r in reversed(srt):  # 新しい順に、FY開示より前で予想のある最初の行
                if _date(r) >= fy_date:
                    continue
                if r.get(fc_key) is not None:
                    standing = r.get(fc_key)
                    break
            out[ach_key] = forecast_achievement(latest_fy.get(actual_key), standing)

        # 上方/下方修正: 進行中FY（最新FY開示より後）の予想の直近 2 開示。FYが無ければ全予想行から
        in_prog = [
            r
            for r in srt
            if r.get(fc_key) is not None and (latest_fy is None or _date(r) > fy_date)
        ]
        if len(in_prog) >= 2:
            out[rev_key] = forecast_revision(in_prog[-1].get(fc_key), in_prog[-2].get(fc_key))

    return out


# --- 売掛/在庫の質シグナル（ADR-064 #2） ---

_DAYS_IN_YEAR = 365.0


def receivables_turnover_days(receivables: float | None, revenue: float | None) -> float | None:
    """売掛金回転日数 DSO = 受取債権 / 売上 × 365（売上に対する受取債権の滞留水準）。

    売上が None・0 以下なら None（成長率と同方針）。受取債権が None も None。値は「何日分の売上が
    受取債権として未回収か」。水準の良し悪し（押し込み/回収悪化の疑い）は同業種比較で LLM が解釈
    （ADR-014）。負の受取債権は通常ないが、来たら事実として返す（捏造しない）。
    """
    if receivables is None or revenue is None or revenue <= 0:
        return None
    return receivables / revenue * _DAYS_IN_YEAR


def inventory_turnover_days(inventory: float | None, cogs: float | None) -> float | None:
    """在庫回転日数 DIO = 棚卸資産 / 売上原価 × 365（原価に対する在庫の滞留水準）。

    分母（売上原価）が None・0 以下なら None。在庫が None も None。売上原価が取れない決算では
    services 層が revenue を代理分母に渡すことがある（その旨は呼び出し側のフォールバック・本関数は
    渡された分母で計算するだけ）。水準の良し悪し（滞留/陳腐化の疑い）は LLM が解釈（ADR-014）。
    """
    if inventory is None or cogs is None or cogs <= 0:
        return None
    return inventory / cogs * _DAYS_IN_YEAR


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
