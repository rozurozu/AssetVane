"""米株 holdings 再計算・評価サービス（ADR-057: us_transactions からの導出値）。

us_transactions が更新されるたびに `recalc_us_holdings` を呼び、その銘柄の us_holdings 行を
入れ替える。us_holdings は直接編集せず、必ずこの関数経由で更新する（ADR-019/057）。単一ユーザー
（ADR-001）ゆえ portfolio で割らず symbol 単位で導出する（日本株 recalc_holdings は portfolio 単位
だが米株は global）。

含み損益は「取得時レート固定の厳密含み損益」（ADR-057）:
  原価 cost_jpy   = shares × avg_cost_jpy（約定時 USDJPY で JPY 固定した移動平均原価）
  評価額 mv_jpy   = shares × close(USD) × 現レート(USDJPY)
  含み損益       = mv_jpy − cost_jpy   → 為替変動も含み損益に乗る

移動平均の畳み込み自体は services/holdings.py の純関数 recompute_positions を株/投信と共有する。
USD 建て avg_cost と JPY 固定 avg_cost_jpy は、同じ畳み込みを **価格キーを変えて 2 度** 適用して
導出する（avg_cost_jpy 用に price_jpy = price × fx_rate を取引行へ事前付与する）。FX 換算自体は
通貨非依存の純関数 value_us_holdings が担う（ADR-014/016: quant は通貨を知らない）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Connection

from app.db import repo
from app.services.holdings import recompute_positions


def recalc_us_holdings(conn: Connection, symbol: str) -> None:
    """指定 symbol の全 us_transactions から us_holdings 行を再導出して入れ替える（ADR-019/057）。

    1. symbol の全 us_transactions を traded_at 昇順で取得。
    2. buy/sell を時系列順に適用し shares と avg_cost(USD) を導出（recompute_positions・株と共有）。
    3. 同じ取引列に price_jpy = price × fx_rate を付け、もう一度畳んで avg_cost_jpy を導出。
    4. shares > 0 なら repo.upsert_us_holding、shares <= 0（全売却）なら repo.delete_us_holding。

    commit はしない。us_transactions 更新と同じ `with get_engine().begin()` 内で呼ぶ（atomic）。
    """
    txns = repo.list_us_transactions(conn, symbol=symbol)

    # ① USD 建ての shares / avg_cost を導出（価格キー=price）。
    state_usd = recompute_positions(txns, key_col="symbol", qty_key="shares", price_key="price")

    # ② 取得時レートで JPY 固定した avg_cost_jpy を、同じ畳み込みを price_jpy で適用して導出する。
    #    price_jpy = price(USD) × fx_rate(約定時 USDJPY)。fx_rate は us_transactions の必須列。
    txns_jpy = [{**t, "price_jpy": float(t["price"]) * float(t["fx_rate"])} for t in txns]
    state_jpy = recompute_positions(
        txns_jpy, key_col="symbol", qty_key="shares", price_key="price_jpy"
    )

    st = state_usd.get(symbol)
    if st is None or st["qty"] <= 0:
        # 全売却 or 取引消滅 → 保有行を残さない（株/投信と同方針）。
        repo.delete_us_holding(conn, symbol)
        return

    repo.upsert_us_holding(
        conn,
        {
            "symbol": symbol,
            "shares": st["qty"],
            "avg_cost": st["avg_cost"],
            "avg_cost_jpy": state_jpy[symbol]["avg_cost"],
        },
    )


def value_us_holdings(
    holdings_rows: list[dict[str, Any]],
    latest_closes_usd: dict[str, dict[str, Any]],
    fx_rate: float | None,
) -> list[dict[str, Any]]:
    """us_holdings 行に JPY 評価額・含み損益・weight を付与する純関数（ADR-014/016/057）。

    引数:
      holdings_rows     … repo.list_us_holdings の返却（symbol/shares/avg_cost/avg_cost_jpy＋名称）
      latest_closes_usd … repo.get_latest_us_closes の返却（symbol -> {date, close}・USD 建て）。
      fx_rate           … 現在の USDJPY（JPY/USD）。None（FX 未取得）なら評価系は全て None。

    付与する列（value_fund_holdings 同方針＝事実が欠けたら関連列は None・捏造しない）:
      last_close        … 最新終値（USD）
      close_date        … その終値の営業日
      fx_rate           … 換算に使った USDJPY（監査・行に焼く）
      market_value_jpy  … shares × close × fx_rate
      cost_jpy          … shares × avg_cost_jpy（取得時レート固定原価・avg_cost_jpy 無しは None）
      unrealized_pnl_jpy… market_value_jpy − cost_jpy（為替損益込み・cost_jpy 無しは None）
      weight            … 米株内合計（us_stock_value）に対する比率（0..1）
    """
    # 米株内の総評価額を先に計算（weight の分母）。close または fx_rate が無い銘柄は寄与しない。
    total_us: float = 0.0
    if fx_rate is not None:
        for h in holdings_rows:
            info = latest_closes_usd.get(h["symbol"])
            if info and info.get("close") is not None:
                total_us += float(h["shares"]) * float(info["close"]) * float(fx_rate)

    valued: list[dict[str, Any]] = []
    for h in holdings_rows:
        info = latest_closes_usd.get(h["symbol"])

        last_close: float | None = None
        close_date: str | None = None
        market_value_jpy: float | None = None
        cost_jpy: float | None = None
        unrealized_pnl_jpy: float | None = None
        weight: float | None = None

        if info and info.get("close") is not None and fx_rate is not None:
            last_close = float(info["close"])
            close_date = info.get("date")
            shares = float(h["shares"])
            market_value_jpy = shares * last_close * float(fx_rate)
            avg_cost_jpy = h.get("avg_cost_jpy")
            if avg_cost_jpy is not None:
                cost_jpy = shares * float(avg_cost_jpy)
                unrealized_pnl_jpy = market_value_jpy - cost_jpy
            if total_us > 0.0:
                weight = market_value_jpy / total_us

        valued.append(
            {
                **h,
                "last_close": last_close,
                "close_date": close_date,
                "fx_rate": fx_rate,
                "market_value_jpy": market_value_jpy,
                "cost_jpy": cost_jpy,
                "unrealized_pnl_jpy": unrealized_pnl_jpy,
                "weight": weight,
            }
        )

    return valued
