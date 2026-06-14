"""日米業種リードラグ（Phase 7(A)・SIG-FIN-036-13・ADR-010/027）。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Connection, select

from app.db.schema import (
    daily_quotes,
    index_quotes,
)

# ===== Phase 7: Lead-Lag（リードラグ・日米業種・SIG-FIN-036-13・ADR-010/027） =====


def get_index_closes_by_symbols(
    conn: Connection, symbols: list[str]
) -> dict[str, list[dict[str, Any]]]:
    """複数 symbol の index_quotes（close）を {symbol: [{date, close}, ...]} で返す（Phase 7）。

    米国業種 ETF（"XLB".."XLRE"・配当調整後 close）を一括で読み、symbol ごとに date 昇順の
    リストにまとめる（lead_lag service が日米共通営業日で整合させる）。close が NULL の行も
    含めて返す（補間しない＝ADR-014・欠損は呼び出し側が NaN 扱い）。symbols が空なら空 dict。
    """
    if not symbols:
        return {}
    stmt = (
        select(index_quotes.c.symbol, index_quotes.c.date, index_quotes.c.close)
        .where(index_quotes.c.symbol.in_(symbols))
        .order_by(index_quotes.c.symbol, index_quotes.c.date)
    )
    out: dict[str, list[dict[str, Any]]] = {s: [] for s in symbols}
    for r in conn.execute(stmt).mappings().all():
        out[r["symbol"]].append({"date": r["date"], "close": r["close"]})
    return out


def get_daily_ohlc_by_codes(conn: Connection, codes: list[str]) -> dict[str, list[dict[str, Any]]]:
    """複数 code の daily_quotes を {code: [{date, open, close, adj_close}]} で返す（Phase 7）。

    日本業種 ETF（DB code は 5桁形・"16170".."16330"）の open / raw close / adj_close を一括で
    読み、code ごとに date 昇順のリストにまとめる（lead_lag service 用）。lead_lag は JP の
    close-to-close に adj_close（トータルリターン）、同日 open-to-close に raw open/close を使う
    （adj_close を raw open と混ぜると毎日ズレるため＝raw 同士で完結させる）。各値が NULL の行も
    含めて返す（補間しない＝ADR-014）。codes が空なら空 dict。
    """
    if not codes:
        return {}
    stmt = (
        select(
            daily_quotes.c.code,
            daily_quotes.c.date,
            daily_quotes.c.open,
            daily_quotes.c.close,
            daily_quotes.c.adj_close,
        )
        .where(daily_quotes.c.code.in_(codes))
        .order_by(daily_quotes.c.code, daily_quotes.c.date)
    )
    out: dict[str, list[dict[str, Any]]] = {c: [] for c in codes}
    for r in conn.execute(stmt).mappings().all():
        out[r["code"]].append(
            {
                "date": r["date"],
                "open": r["open"],
                "close": r["close"],
                "adj_close": r["adj_close"],
            }
        )
    return out
