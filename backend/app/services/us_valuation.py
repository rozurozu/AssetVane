"""米株バリュエーション・スナップショットの下ごしらえ＋組み立て（ADR-014/016・ADR-031/048/055）。

設計の真実: docs/decisions.md ADR-031（スクリーナー設計）・ADR-048（ROE/利益率/成長率）・
ADR-055（米株は GICS 相当・YoY は取れる率だけ採る）。

日本株 services/valuation.py をミラーした米株版（既存無改変）。repo（us_stocks の財務素・
最新終値）と quant.valuation（純関数）の間に立ち、us_valuation_snapshots 行を組み立てる。
**数値計算そのものは quant.valuation の純関数に委ね、別計算式を持ち込まない（ADR-014/016）。**

採用行の規律（日本株と違い米株は yfinance `.info` スナップショット 1 点が素＝財務行の履歴を
持たない）:
- PER/PBR/利益率/ROE/時価総額/配当利回りは us_stocks の財務素（adapter が `.info` から焼いた
  eps/bps/shares_net/dividend_per_share/net_sales/operating_profit/profit）× 最新 close で計算。
- close の無い銘柄（OHLCV 未取得）も**行は作る**（指標は None）。日本株 build と同じ「価格が無く
  ても行は作る／財務が無くても行は作る」の二方向の網羅性を保つ（スクリーナーで NULL は除外可）。

YoY 成長率の確定方針（ADR-055・統括判断で「取れる率を活かす」）:
- `.info` は前期 FY 値を持たないため、`growth_yoy` 純関数を当てる素（当期/前期の対）は持てない。
  だが `.info` 自身が提供する YoY 率（revenueGrowth/earningsGrowth）は実値であり捏造ではない。
  fetch_us_fundamentals が us_stocks の中継列（revenue_growth_yoy/earnings_growth_yoy）に焼くので、
  ここはそれを us_valuation_snapshots へ厳密に転記する（別計算式を作らない）。
- 転記の厳密対応:
    * revenue_growth_yoy ← us_stocks.revenue_growth_yoy（売上 YoY＝`.info.revenueGrowth`）。
    * profit_growth_yoy  ← us_stocks.earnings_growth_yoy（純利益 YoY＝`.info.earningsGrowth`。
      earningsGrowth は「純利益成長」なので EPS ではなく純利益＝profit 軸に対応させる）。
    * op_growth_yoy      ← None（営業利益 YoY の素が `.info` に無い）。
    * eps_growth_yoy     ← None（EPS の前期値を持たない＝EPS 厳密 YoY は組めない）。
- screen の range/sort allowlist は YoY 4 列を含むが、revenue/profit 軸は実値で機能し、op/eps 軸は
  全銘柄 NULL のため絞り込みで自然に対象外になる（壊れない）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection

from app.db import repo
from app.quant import valuation


def build_us_valuation_snapshots(conn: Connection) -> list[dict[str, Any]]:
    """全 us_stocks について us_valuation_snapshots 行を組んで返す（焼くのは呼び出し側）。

    us_stocks の財務素 × 最新 close を quant.valuation の純関数で畳む（PER/PBR/時価総額/利回り/
    ROE/営業利益率/純利益率）。close の無い銘柄も行は作る（指標 None）。YoY は us_stocks の中継列
    から厳密転記する＝revenue_growth_yoy ← revenue_growth_yoy（売上）・profit_growth_yoy ←
    earnings_growth_yoy（純利益）・op/eps は素なしで None（ADR-055・モジュール docstring 参照）。
    """
    masters = repo.list_us_stocks(conn)
    if not masters:
        return []
    closes = repo.get_latest_us_closes(conn)  # {symbol: {date, close}}（全銘柄・最新営業日）
    now = datetime.now(UTC).isoformat()

    rows: list[dict[str, Any]] = []
    for m in masters:
        symbol = m["symbol"]
        price = closes.get(symbol)
        close = price["close"] if price else None
        as_of = price["date"] if price else None

        eps = m.get("eps")
        bps = m.get("bps")
        dps = m.get("dividend_per_share")
        shares_net = m.get("shares_net")
        sales = m.get("net_sales")
        op = m.get("operating_profit")
        profit = m.get("profit")

        # quant.valuation の純関数を再利用（別計算式を持ち込まない＝ADR-014/016）。
        metrics = valuation.compute_valuation(
            close=close, eps=eps, bps=bps, dividend_per_share=dps, shares_net=shares_net
        )
        rows.append(
            {
                "symbol": symbol,
                "as_of_date": as_of or now[:10],  # 価格未取得は焼成日（NOT NULL 制約のため）
                "close": close,
                "eps": eps,
                "bps": bps,
                "dividend_per_share": dps,
                "shares_net": shares_net,
                "per": metrics["per"],
                "pbr": metrics["pbr"],
                "market_cap": metrics["market_cap"],
                "dividend_yield": metrics["dividend_yield"],
                "roe": valuation.roe(eps, bps),
                "operating_margin": valuation.operating_margin(op, sales),
                "net_margin": valuation.net_margin(profit, sales),
                # YoY は us_stocks の `.info` 中継列から厳密転記（ADR-055・捏造しない）。
                "revenue_growth_yoy": m.get("revenue_growth_yoy"),  # 売上 YoY（revenueGrowth）
                "profit_growth_yoy": m.get("earnings_growth_yoy"),  # 純利益 YoY（earningsGrowth）
                "op_growth_yoy": None,  # 営業利益 YoY の素なし
                "eps_growth_yoy": None,  # EPS 前期値を持たない
                "fin_disclosed_date": m.get("fin_disclosed_date"),
                "updated_at": now,
            }
        )
    return rows
