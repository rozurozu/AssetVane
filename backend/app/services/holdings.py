"""holdings 再計算サービス（ADR-019: transactions からの導出値）。

transactions が更新されるたびに `recalc_holdings` を呼び、holdings を入れ替える。
holdings は直接編集せず、必ずこの関数経由で更新する（phase2-spec.md §1「重要な設計判断」）。

avg_cost（移動平均取得単価）の計算方法:
  buy 時: new_avg = (old_shares * old_avg + buy_shares * buy_price) / (old_shares + buy_shares)
  sell 時: avg は変えず shares だけ減らす。
  全売却(shares<=0): holdings 行を保存しない（repo.replace_holdings が非ゼロ行のみ渡す）。
  ※ fee（手数料）は avg_cost 計算に含めない（spec §1「重要な設計判断」に注記）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Connection

from app.db import repo


def recompute_positions(
    transactions: list[dict[str, Any]],
    key_col: str = "code",
    qty_key: str = "shares",
    price_key: str = "price",
) -> dict[str, dict[str, float]]:
    """取引列を時系列順に畳んで銘柄ごとの数量・移動平均取得単価を導出する純関数（ADR-019）。

    株式（key_col='code'・qty_key='shares'）と投信（key_col='isin'・qty_key='units'）で共有する
    （services/fund_holdings.py からも呼ぶ）。DB を知らない・副作用なし（services/quant 規約）。
    transactions は traded_at 昇順済みであること（呼び出し側 repo が昇順取得する）。

    返却: key（code/isin）-> {"qty": float, "avg_cost": float}。
      buy 時: new_avg = (old_qty * old_avg + buy_qty * buy_price) / (old_qty + buy_qty)
      sell 時: avg は変えず qty だけ減らす（0 未満には下げない）。
      fee は avg_cost 計算に含めない（一次データの約定価格のみで導出・phase2-spec.md §1）。
    """
    state: dict[str, dict[str, float]] = {}

    for txn in transactions:
        key: str = txn[key_col]
        side: str = txn["side"]
        qty: float = float(txn[qty_key])
        price: float = float(txn[price_key])

        if key not in state:
            state[key] = {"qty": 0.0, "avg_cost": 0.0}

        cur = state[key]
        if side == "buy":
            # 移動平均取得単価を更新（fee は含めない）
            old_qty = cur["qty"]
            old_avg = cur["avg_cost"]
            new_qty = old_qty + qty
            if new_qty > 0:
                cur["avg_cost"] = (old_qty * old_avg + qty * price) / new_qty
            cur["qty"] = new_qty
        elif side == "sell":
            # sell は avg_cost を変えず qty だけ減らす
            cur["qty"] = max(0.0, cur["qty"] - qty)

    return state


def recalc_holdings(conn: Connection, portfolio_id: int) -> None:
    """指定ポートフォリオの全 transactions から holdings を再導出して入れ替える。

    1. portfolio_id の全 transactions を traded_at 昇順で取得。
    2. 銘柄ごとに buy/sell を時系列順に適用し、shares と avg_cost を導出
       （recompute_positions・投信 fund_holdings と共有の純関数）。
    3. shares > 0 の銘柄のみ repo.replace_holdings で保存（全売却行は除外）。
    （ADR-019 holdings は transactions から導出・phase2-spec.md §1）

    commit はしない。transactions 更新と同じ `with get_engine().begin()` 内で呼ぶ。
    """
    txns = repo.list_transactions(conn, portfolio_id)
    state = recompute_positions(txns, key_col="code", qty_key="shares", price_key="price")

    # shares > 0 の行のみ保存（全売却は行を残さない）
    rows = [
        {
            "portfolio_id": portfolio_id,
            "code": code,
            "shares": st["qty"],
            "avg_cost": st["avg_cost"] if st["qty"] > 0 else None,
        }
        for code, st in state.items()
        if st["qty"] > 0
    ]

    repo.replace_holdings(conn, portfolio_id, rows)
