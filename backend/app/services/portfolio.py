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
from app.quant import backtest_portfolio, compute_deviations
from app.services.policy import get_policy

# backtest の既定ベンチマーク（TOPIX）。^TPX は JQuantsIndexSource 経由で index_quotes に入る
# （ADR-040）。マジック値を散らさずモジュール定数にする（ADR-027）。
DEFAULT_BENCHMARK_SYMBOL = "^TPX"


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
