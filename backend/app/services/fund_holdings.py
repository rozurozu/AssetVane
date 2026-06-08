"""投信 holdings 再計算サービス（ADR-054: fund_transactions からの導出値）。

fund_transactions が更新されるたびに `recalc_fund_holdings` を呼び、fund_holdings を
入れ替える。fund_holdings は直接編集せず、必ずこの関数経由で更新する（ADR-019/054）。

avg_cost（移動平均取得単価・10,000 口あたりの円）の計算方法は株式 holdings と同型で、
口数（units）を数量に使う:
  buy 時: new_avg = (old_units * old_avg + buy_units * buy_price) / (old_units + buy_units)
  sell 時: avg は変えず units だけ減らす。
  全売却(units<=0): fund_holdings 行を保存しない。
  ※ fee（手数料）は avg_cost 計算に含めない（株式 holdings と同方針）。

移動平均の畳み込み自体は services/holdings.py の純関数 recompute_positions を株と共有する
（数量キー=units・価格キー=price で呼ぶ）。投信特有の評価額換算（10,000 口あたり）は
value_fund_holdings が担う。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Connection

from app.db import repo
from app.services.holdings import recompute_positions

# 投信の基準価額・取得単価は「10,000 口あたりの円」で扱う（ADR-054・data-model.md §投資信託）。
# 評価額 = units / FUND_UNIT_BASE * nav。マジック値を散らさずモジュール定数にする（ADR-027）。
FUND_UNIT_BASE = 10_000.0


def recalc_fund_holdings(conn: Connection, portfolio_id: int) -> None:
    """指定ポートフォリオの全 fund_transactions から fund_holdings を再導出して入れ替える。

    1. portfolio_id の全 fund_transactions を traded_at 昇順で取得。
    2. 銘柄（isin）ごとに buy/sell を時系列順に適用し、units と avg_cost を導出
       （recompute_positions・株式 holdings と共有の純関数）。
    3. units > 0 の銘柄のみ repo.replace_fund_holdings で保存（全売却行は除外）。
    （ADR-019/054 fund_holdings は fund_transactions から導出）

    commit はしない。fund_transactions 更新と同じ `with get_engine().begin()` 内で呼ぶ。
    """
    txns = repo.list_fund_transactions(conn, portfolio_id)
    state = recompute_positions(txns, key_col="isin", qty_key="units", price_key="price")

    rows: list[dict[str, Any]] = [
        {
            "portfolio_id": portfolio_id,
            "isin": isin,
            "units": st["qty"],
            "avg_cost": st["avg_cost"] if st["qty"] > 0 else None,
        }
        for isin, st in state.items()
        if st["qty"] > 0
    ]
    repo.replace_fund_holdings(conn, portfolio_id, rows)


def value_fund_holdings(
    holdings_rows: list[dict[str, Any]],
    latest_navs: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """fund_holdings 行に market_value / unrealized_pnl / weight / last_nav / nav_date を付与する。

    latest_navs: repo.get_latest_fund_navs の返却値（isin -> {date, nav}）。
    10,000 口あたり換算を必ず守る（ADR-054）:
      market_value   = units / 10000 * nav
      unrealized_pnl = units / 10000 * (nav - avg_cost)  ※ avg_cost なしは None
    weight は投信内合計（fund_value）に対する比率（0..1）。nav 不明な銘柄は関連列を None にする
    （value_holdings と同方針）。
    """
    # 投信内の総評価額を先に計算（weight の分母）。
    total_fund: float = 0.0
    for h in holdings_rows:
        nav_info = latest_navs.get(h["isin"])
        if nav_info and nav_info.get("nav") is not None:
            total_fund += float(h["units"]) / FUND_UNIT_BASE * float(nav_info["nav"])

    valued: list[dict[str, Any]] = []
    for h in holdings_rows:
        nav_info = latest_navs.get(h["isin"])

        last_nav: float | None = None
        nav_date: str | None = None
        market_value: float | None = None
        unrealized_pnl: float | None = None
        weight: float | None = None

        if nav_info and nav_info.get("nav") is not None:
            last_nav = float(nav_info["nav"])
            nav_date = nav_info.get("date")
            units = float(h["units"])
            market_value = units / FUND_UNIT_BASE * last_nav
            avg_cost = h.get("avg_cost")
            if avg_cost is not None:
                unrealized_pnl = units / FUND_UNIT_BASE * (last_nav - float(avg_cost))
            if total_fund > 0.0:
                weight = market_value / total_fund

        valued.append(
            {
                **h,
                "last_nav": last_nav,
                "nav_date": nav_date,
                "market_value": market_value,
                "unrealized_pnl": unrealized_pnl,
                "weight": weight,
            }
        )

    return valued
