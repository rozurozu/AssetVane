"""portfolio サービス — quant 呼び出し前の下ごしらえ。

price_panel（adj_close パネル）の構築と、保有評価額の計算を担う。
数値計算は quant 純関数に委ねる（ADR-014: AI/ここで計算しない）。
（phase2-spec.md §1・ADR-005・ADR-014）
"""

from __future__ import annotations

from typing import Any

import pandas as pd
from sqlalchemy import Connection

from app.db import repo
from app.quant import (
    backtest_portfolio,
    compute_deviations,
    compute_portfolio_metrics,
    compute_risk_contributions,
)
from app.services.policy import get_policy

# backtest の既定ベンチマーク（TOPIX）。^TPX は JQuantsIndexSource 経由で index_quotes に入る
# （ADR-040）。マジック値を散らさずモジュール定数にする（ADR-027）。
DEFAULT_BENCHMARK_SYMBOL = "^TPX"

# what-if の相関・リスク寄与に載せる上位件数（マジック値を散らさない＝ADR-027）。
_IMPACT_TOP_N = 5


def build_price_panel(conn: Connection, codes: list[str]) -> pd.DataFrame:
    """codes の adj_close を日次パネル DataFrame で返す。

    index=date(str), columns=code, 値=adj_close。
    各 code の全日付を外部結合し、欠損は NaN のまま残す（補間しない＝裁定 L-26）。
    codes が空の場合は空 DataFrame を返す。
    （phase2-spec.md §4.1・ADR-016）
    """
    if not codes:
        return pd.DataFrame()

    frames: dict[str, pd.Series] = {}
    for code in codes:
        quotes = repo.get_quotes(conn, code)
        if not quotes:
            continue
        series = pd.Series(
            {q["date"]: q["adj_close"] for q in quotes},
            name=code,
            dtype=float,
        )
        frames[code] = series

    if not frames:
        return pd.DataFrame()

    # 各系列を外部結合して DataFrame に組み立てる
    panel = pd.concat(frames, axis=1)
    panel.index = pd.Index(sorted(panel.index))  # date 昇順
    return panel


def value_holdings(
    holdings_rows: list[dict[str, Any]],
    latest_closes: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """holdings 行に market_value / unrealized_pnl / weight を付与して返す。

    latest_closes: repo.get_latest_closes の返却値（code -> {date, close}）。
    weight は株式内の比率（0..1）。last_close が無い銘柄は関連列を null にする。
    （phase2-spec.md §5 P2-2 Holding 型）
    """
    # 総株式評価額を先に計算（weight 分母）
    total_stock: float = 0.0
    for h in holdings_rows:
        code = h["code"]
        close_info = latest_closes.get(code)
        if close_info:
            total_stock += float(h["shares"]) * float(close_info["close"])

    valued: list[dict[str, Any]] = []
    for h in holdings_rows:
        code = h["code"]
        close_info = latest_closes.get(code)

        last_close: float | None = None
        market_value: float | None = None
        unrealized_pnl: float | None = None
        weight: float | None = None

        if close_info:
            last_close = float(close_info["close"])
            market_value = float(h["shares"]) * last_close
            avg_cost = h.get("avg_cost")
            if avg_cost is not None:
                unrealized_pnl = market_value - float(h["shares"]) * float(avg_cost)
            if total_stock > 0.0:
                weight = market_value / total_stock

        valued.append(
            {
                **h,
                "last_close": last_close,
                "market_value": market_value,
                "unrealized_pnl": unrealized_pnl,
                "weight": weight,
            }
        )

    return valued


def current_stock_weights(holdings_valued: list[dict[str, Any]]) -> dict[str, float]:
    """株式内の現在ウェイト（0..1）を dict[code, weight] で返す。

    weight が null の銘柄は除外する（last_close 不明で計算不能）。
    （phase2-spec.md §5 P2-5・metrics/optimize の入力に使う）
    """
    return {h["code"]: float(h["weight"]) for h in holdings_valued if h.get("weight") is not None}


def portfolio_deviations(conn: Connection, portfolio_id: int) -> list[dict[str, Any]]:
    """policy 逸脱（deviations）を quant の単一関数で計算する（決定6・B-12）。

    **`/asset-overview`（画面）と `/portfolio/{id}/metrics`（Tool）の両方がこの 1 関数を呼び、
    同一の入力から同値の deviations を得る**（計算 1 か所・出力先 2 つ）。自前で別々に組むと
    metrics 側が現金文脈を持てず食い違うため、ここに一本化する。

    入力の基準（注記・統一）:
    - `weights`（銘柄ウェイト）・`sector_weights`（業種別合計）は**株式内 0..1**。
    - `cash_ratio` は**全資産内**（cash_value / total_value）。
      `target_cash_ratio` も全資産内 25% 想定。
    （phase2-spec.md §4.2・§5 P2-5/P2-7・ADR-013/ADR-014）
    """
    holdings_rows = repo.list_holdings(conn, portfolio_id)
    codes = [h["code"] for h in holdings_rows]
    latest_closes = repo.get_latest_closes(conn, codes) if codes else {}
    holdings_valued = value_holdings(holdings_rows, latest_closes)

    # 株式内ウェイト・業種別合計（0..1）
    weights: dict[str, float] = {}
    sector_weights: dict[str, float] = {}
    for h in holdings_valued:
        if h.get("weight") is None:
            continue
        w = float(h["weight"])
        weights[h["code"]] = w
        sec = h.get("sector33_code") or ""
        if sec:
            sector_weights[sec] = sector_weights.get(sec, 0.0) + w

    # 現金比率は全資産内（株式評価額＋現金＋外部資産が分母）
    stock_value = sum(
        float(h["market_value"]) for h in holdings_valued if h.get("market_value") is not None
    )
    cash_row = repo.get_cash(conn)
    cash_value = float(cash_row["balance"]) if cash_row else 0.0
    ext_rows = repo.list_external_assets(conn)
    external_value = sum(float(r["value"]) for r in ext_rows if r.get("value") is not None)
    total_value = stock_value + cash_value + external_value
    cash_ratio = cash_value / total_value if total_value > 0 else 0.0

    labels = {h["code"]: h.get("company_name") or h["code"] for h in holdings_valued}
    return compute_deviations(
        weights=weights,
        cash_ratio=cash_ratio,
        sector_weights=sector_weights,
        policy=get_policy(conn),
        labels=labels,
    )


def _target_correlations(
    correlation: dict[str, Any] | None, code: str, labels: dict[str, str]
) -> list[dict[str, Any]]:
    """pro-forma 相関行列から対象銘柄の行を取り、他銘柄との相関を |corr| 降順で返す（ADR-085）。"""
    if not correlation:
        return []
    codes = correlation.get("codes") or []
    matrix = correlation.get("matrix") or []
    if code not in codes:
        return []
    i = codes.index(code)
    out: list[dict[str, Any]] = []
    for j, other in enumerate(codes):
        if other == code:
            continue
        out.append(
            {
                "code": other,
                "company_name": labels.get(other, other),
                "correlation": float(matrix[i][j]),
            }
        )
    out.sort(key=lambda d: abs(d["correlation"]), reverse=True)
    return out[:_IMPACT_TOP_N]


def _target_risk_contribution(
    risk: dict[str, Any], code: str, labels: dict[str, str]
) -> dict[str, Any]:
    """compute_risk_contributions の結果から対象銘柄の寄与と上位寄与銘柄を整形する（ADR-085）。"""
    contributions = risk.get("contributions") or []
    top = [
        {
            "code": c["code"],
            "company_name": labels.get(c["code"], c["code"]),
            "percent": float(c["percent"]),
        }
        for c in contributions[:_IMPACT_TOP_N]
    ]
    target = next((c for c in contributions if c["code"] == code), None)
    if target is None:
        # 価格履歴が足りず対象が寄与分解に載らない → 数値は None（捏造しない・ADR-014）。
        return {"component": None, "percent": None, "marginal": None, "top_contributors": top}
    return {
        "component": float(target["component"]),
        "percent": float(target["percent"]),
        "marginal": float(target["marginal"]),
        "top_contributors": top,
    }


def simulate_trade_impact(
    conn: Connection,
    *,
    portfolio_id: int,
    action: str,
    code: str,
    amount_jpy: float,
) -> dict[str, Any]:
    """仮の買い/売り（amount_jpy 円）の現ポートフォリオへの影響を pro-forma で返す（#4・ADR-085）。

    propose_trade は size を持たない（サイズは AI に決めさせない＝ADR-014）。本関数の amount_jpy は
    「もし入れたら」を試算するための仮サイズで、**永続も発注もしない**。現保有評価に仮の delta を
    混ぜて pro-forma のウェイト・現金比率・業種比率を作り、集中度（compute_deviations）・年率 vol/
    相関（compute_portfolio_metrics）・リスク寄与（compute_risk_contributions）を現状/pro-forma で
    比較する（計算はすべて quant 純関数＝ADR-016）。

    日本株のみ（市場分離＝ADR-031）。US・未知コードは found:False＋note で落とさず返す（ADR-018）。
    is_delayed は付けない（呼び出し側が as_of から判定＝ADR-071・handler が付与）。
    """
    code = code.strip()
    jp = repo.get_stock(conn, code)
    if jp is None:
        # US・未知は found:False で返す（幻覚/市場外を捏造せず落とす・ADR-018）。
        market = "US" if repo.get_us_stock(conn, code) is not None else None
        note = (
            "米国株は対象外（この what-if は日本株のみ・ADR-031）"
            if market == "US"
            else f"銘柄 {code} が見つからない（JP 5 桁コードを確認）"
        )
        return {
            "portfolio_id": portfolio_id,
            "as_of": None,
            "action": action,
            "code": code,
            "company_name": None,
            "market": market,
            "amount_jpy": float(amount_jpy),
            "found": False,
            "position": None,
            "concentration": None,
            "correlation_to_holdings": [],
            "portfolio_volatility": None,
            "risk_contribution": None,
            "notes": [note],
        }

    notes: list[str] = []
    company_name = str(jp.get("company_name") or "")
    target_sector = jp.get("sector33_code") or ""

    holdings_rows = repo.list_holdings(conn, portfolio_id)
    panel_codes = sorted({h["code"] for h in holdings_rows} | {code})
    price_panel = build_price_panel(conn, panel_codes)
    latest_closes = repo.get_latest_closes(conn, panel_codes)
    valued = value_holdings(holdings_rows, latest_closes)
    current_weights = current_stock_weights(valued)

    # 現保有の時価（market_value がある行だけ）。
    mv_current: dict[str, float] = {
        h["code"]: float(h["market_value"]) for h in valued if h.get("market_value") is not None
    }
    target_mv_current = mv_current.get(code, 0.0)

    # 仮 delta を混ぜた pro-forma 時価（amount_jpy は円なので価格を介さず一意に決まる＝ADR-085）。
    if action == "buy":
        target_mv_new = target_mv_current + float(amount_jpy)
        traded = float(amount_jpy)
    else:  # sell
        traded = min(float(amount_jpy), target_mv_current)
        target_mv_new = target_mv_current - traded
        if float(amount_jpy) > target_mv_current:
            notes.append(
                "売却額が保有評価額を上回るため全部売却に丸めた（負のポジションは作らない）"
            )
        if target_mv_current <= 0.0:
            notes.append("対象は未保有か評価額を算出できないため、売却の影響は限定的")

    mv_pf = dict(mv_current)
    mv_pf[code] = target_mv_new
    total_stock_pf = sum(mv_pf.values())
    pf_weights = {c: v / total_stock_pf for c, v in mv_pf.items()} if total_stock_pf > 0.0 else {}

    # 業種・ラベル（pro-forma 用）。
    sector_map: dict[str, str] = {h["code"]: (h.get("sector33_code") or "") for h in holdings_rows}
    sector_map[code] = target_sector
    labels: dict[str, str] = {
        h["code"]: (h.get("company_name") or h["code"]) for h in holdings_rows
    }
    labels[code] = company_name or code

    pf_sector_weights: dict[str, float] = {}
    for c, w in pf_weights.items():
        sec = sector_map.get(c) or ""
        if sec:
            pf_sector_weights[sec] = pf_sector_weights.get(sec, 0.0) + w

    # 現金比率は全資産内（buy=現金→株・sell=株→現金で total_value は不変＝ADR-013 の分母定義）。
    cash_row = repo.get_cash(conn)
    cash_value = float(cash_row["balance"]) if cash_row else 0.0
    external_value = sum(
        float(r["value"]) for r in repo.list_external_assets(conn) if r.get("value") is not None
    )
    stock_value_current = sum(mv_current.values())
    total_value = stock_value_current + cash_value + external_value
    if action == "buy":
        cash_pf = cash_value - float(amount_jpy)
        if cash_pf < 0.0:
            notes.append("買付額が現金残高を上回る（現金比率は負で表示・要資金手当て）")
    else:
        cash_pf = cash_value + traded
    cash_ratio_pf = cash_pf / total_value if total_value > 0.0 else 0.0

    policy = get_policy(conn)
    concentration_current = portfolio_deviations(conn, portfolio_id)
    concentration_pf = compute_deviations(
        weights=pf_weights,
        cash_ratio=cash_ratio_pf,
        sector_weights=pf_sector_weights,
        policy=policy,
        labels=labels,
    )

    # 年率 vol（現状/pro-forma）・相関・寄与（pro-forma）を計算する。
    # policy=None を渡し deviations の重複計算を避ける（集中度は上で compute_deviations 済み）。
    metrics_current = compute_portfolio_metrics(price_panel, current_weights, None, labels)
    metrics_pf = compute_portfolio_metrics(price_panel, pf_weights, None, labels)
    risk = compute_risk_contributions(price_panel, pf_weights)

    vol_current = metrics_current.get("annual_volatility")
    vol_pf = metrics_pf.get("annual_volatility")
    vol_delta = vol_pf - vol_current if vol_current is not None and vol_pf is not None else None

    correlation_to_holdings = _target_correlations(metrics_pf.get("correlation"), code, labels)
    if not correlation_to_holdings and holdings_rows and target_mv_new > 0.0:
        notes.append("価格履歴が足りず相関・リスク寄与は算出できない（数値は捏造しない）")

    return {
        "portfolio_id": portfolio_id,
        "as_of": metrics_pf.get("as_of"),
        "action": action,
        "code": code,
        "company_name": company_name or None,
        "market": "JP",
        "amount_jpy": float(amount_jpy),
        "found": True,
        "position": {
            "current_weight": current_weights.get(code),
            "proforma_weight": pf_weights.get(code),
            "current_value_jpy": target_mv_current,
            "proforma_value_jpy": target_mv_new,
        },
        "concentration": {"current": concentration_current, "proforma": concentration_pf},
        "correlation_to_holdings": correlation_to_holdings,
        "portfolio_volatility": {
            "current_annual": vol_current,
            "proforma_annual": vol_pf,
            "delta": vol_delta,
        },
        "risk_contribution": _target_risk_contribution(risk, code, labels),
        "notes": notes,
    }


def backtest_portfolio_service(
    conn: Connection,
    portfolio_id: int,
    benchmark_symbol: str = DEFAULT_BENCHMARK_SYMBOL,
) -> dict[str, Any]:
    """保有ポートフォリオの buy&hold バックテストを対指数で計算して返す。

    現在保有ウェイトの買い持ちを benchmark（既定 TOPIX=^TPX）と比較する
    （phase2-spec.md §4.4・§8）。metrics と同じ下ごしらえ（price_panel・現ウェイト）を
    再利用し、計算自体は quant 純関数 backtest_portfolio に委ねる（ADR-014/016）。

    保有 0・履歴不足・benchmark 未取得は純関数が空 leg（as_of=None / curve=[]）を
    返すのでそのまま通す（エラーにしない）。
    """
    holdings_rows = repo.list_holdings(conn, portfolio_id)
    codes = [h["code"] for h in holdings_rows]

    price_panel = build_price_panel(conn, codes)
    latest_closes = repo.get_latest_closes(conn, codes) if codes else {}
    weights = current_stock_weights(value_holdings(holdings_rows, latest_closes))

    bench_quotes = repo.get_index_quotes(conn, benchmark_symbol)
    benchmark = pd.Series(
        {q["date"]: q["close"] for q in bench_quotes},
        dtype=float,
    )

    return backtest_portfolio(price_panel, weights, benchmark, rebalance="none")
