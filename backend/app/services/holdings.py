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

from sqlalchemy import Connection

from app.db import repo


def recalc_holdings(conn: Connection, portfolio_id: int) -> None:
    """指定ポートフォリオの全 transactions から holdings を再導出して入れ替える。

    1. portfolio_id の全 transactions を traded_at 昇順で取得。
    2. 銘柄ごとに buy/sell を時系列順に適用し、shares と avg_cost を導出。
    3. shares > 0 の銘柄のみ repo.replace_holdings で保存（全売却行は除外）。
    （ADR-019 holdings は transactions から導出・phase2-spec.md §1）

    commit はしない。transactions 更新と同じ `with get_engine().begin()` 内で呼ぶ。
    """
    txns = repo.list_transactions(conn, portfolio_id)

    # 銘柄ごとに buy/sell を時系列順で処理
    # state: code -> {"shares": float, "avg_cost": float}
    state: dict[str, dict[str, float]] = {}

    for txn in txns:
        code: str = txn["code"]
        side: str = txn["side"]
        qty: float = float(txn["shares"])
        price: float = float(txn["price"])

        if code not in state:
            state[code] = {"shares": 0.0, "avg_cost": 0.0}

        cur = state[code]
        if side == "buy":
            # 移動平均取得単価を更新（fee は含めない）
            old_shares = cur["shares"]
            old_avg = cur["avg_cost"]
            new_shares = old_shares + qty
            if new_shares > 0:
                cur["avg_cost"] = (old_shares * old_avg + qty * price) / new_shares
            cur["shares"] = new_shares
        elif side == "sell":
            # sell は avg_cost を変えず shares だけ減らす
            cur["shares"] = max(0.0, cur["shares"] - qty)

    # shares > 0 の行のみ保存（全売却は行を残さない）
    rows = [
        {
            "portfolio_id": portfolio_id,
            "code": code,
            "shares": st["shares"],
            "avg_cost": st["avg_cost"] if st["shares"] > 0 else None,
        }
        for code, st in state.items()
        if st["shares"] > 0
    ]

    repo.replace_holdings(conn, portfolio_id, rows)
