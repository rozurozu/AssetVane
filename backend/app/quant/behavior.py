"""投資家の行動の癖を台帳から計算する純関数（ADR-082・投資家プロファイル・★4 自己改善ループ）。

ADR-014/016 の計算境界: DB を知らない純関数（Connection/repo を import しない・副作用なし）。
services/investor_behavior が repo の dict を整えて呼び、結果を素材へ整形する。数値は捏造せず、
データ不足は安全な既定（None/空）を返す（backend-service-quant-pattern）。

信号:
  ①summarize_sell_regret … SELL 後のフォワードリターン（compute_horizon_outcome の final 群）から
    「売った後に上がった率」＝手仕舞い早すぎの癖を集計。
  ②match_round_trips / summarize_disposition … 買い→売りを FIFO で突合し、勝ちトレードと負け
    トレードの保有日数差（勝ち急ぎ・損塩漬け＝ディスポジション効果）を測る。
  ③summarize_concentration … buy のセクター/テーマ集中を降順整形。
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Callable
from datetime import date
from typing import Any


def _finite(value: Any) -> float | None:
    """数値かつ有限なら float、そうでなければ None（NaN/inf/非数は除外・事実を捏造しない）。"""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


# ---- ① 手仕舞いの帰結 -------------------------------------------------------------------


def summarize_sell_regret(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    """SELL の horizon outcome 群から「売った後に上がった率」を集計する（ADR-082 信号①）。

    outcomes は compute_horizon_outcome（ADR-077）の返り値の list（pending/final 混在）。final の
    うち realized_return>0 の割合＝recover_rate（手仕舞いが早すぎた率）。excess はベンチ欠測（None）
    を母数から外して excess_recover_rate を出す。price できた final ゼロなら率・平均は None。
    """
    finals = [o for o in outcomes if o.get("status") == "final"]
    n_pending = sum(1 for o in outcomes if o.get("status") == "pending")
    realized = [r for o in finals if (r := _finite(o.get("realized_return"))) is not None]
    excess = [e for o in finals if (e := _finite(o.get("excess_return"))) is not None]

    def _rate(values: list[float], predicate: Callable[[float], bool]) -> float | None:
        return sum(1 for v in values if predicate(v)) / len(values) if values else None

    def _avg(values: list[float]) -> float | None:
        return sum(values) / len(values) if values else None

    return {
        "n_final": len(finals),
        "n_pending": n_pending,
        "recover_rate": _rate(realized, lambda v: v > 0.0),
        "excess_recover_rate": _rate(excess, lambda v: v > 0.0),
        "avg_realized_return": _avg(realized),
        "avg_excess_return": _avg(excess),
        "n_excess": len(excess),
    }


# ---- ② ディスポジション効果 -------------------------------------------------------------


def _calendar_days(buy_date: str, sell_date: str) -> int | None:
    """暦日差（sell - buy）を返す。日付が不正なら None（保有日数を捏造しない）。"""
    try:
        return (date.fromisoformat(sell_date) - date.fromisoformat(buy_date)).days
    except ValueError:
        return None


def _make_trip(
    code: str, buy_price: float, buy_date: str, sell_price: float, sell_date: str, shares: float
) -> dict[str, Any]:
    return {
        "code": code,
        "buy_date": buy_date,
        "sell_date": sell_date,
        "shares": shares,
        "buy_price": buy_price,
        "sell_price": sell_price,
        "holding_days": _calendar_days(buy_date, sell_date),
        "is_win": sell_price > buy_price,
        "pnl": (sell_price - buy_price) * shares,
    }


def match_round_trips(txns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """buy→sell を FIFO で突合し、実現した往復（ロット単位）の list を返す（ADR-082 信号②）。

    txns: [{code, side('buy'/'sell'), shares, price, traded_at('YYYY-MM-DD')}]。code ごとに
    traded_at 昇順で畳み、買いロットの FIFO キューを売りが消費する。ロット分割ごとに 1 イベント。
    買いを超える売り（空売り/台帳欠落）は未対応分を捨てる（例外で落とさない）。移動平均でなく
    FIFO＝ディスポジション効果の標準測定（Odean 1998）で、勝ち負けと保有日数を lot 単位で分ける。
    """
    by_code: dict[str, list[dict[str, Any]]] = {}
    for t in txns:
        by_code.setdefault(str(t.get("code")), []).append(t)

    trips: list[dict[str, Any]] = []
    for code, rows in by_code.items():
        rows_sorted = sorted(rows, key=lambda r: str(r.get("traded_at")))
        lots: deque[list[Any]] = deque()  # [shares, price, date] の可変ロット（FIFO）
        for r in rows_sorted:
            side = str(r.get("side"))
            shares = _finite(r.get("shares"))
            price = _finite(r.get("price"))
            traded_at = str(r.get("traded_at"))
            if shares is None or price is None or shares <= 0:
                continue
            if side == "buy":
                lots.append([shares, price, traded_at])
            elif side == "sell":
                remaining = shares
                while remaining > 1e-9 and lots:
                    lot = lots[0]
                    take = min(remaining, lot[0])
                    trips.append(_make_trip(code, lot[1], lot[2], price, traded_at, take))
                    lot[0] -= take
                    remaining -= take
                    if lot[0] <= 1e-9:
                        lots.popleft()
                # remaining>0（対応買いなし）は捨てる
    return trips


def summarize_disposition(round_trips: list[dict[str, Any]]) -> dict[str, Any]:
    """往復群から勝ち/負けの平均保有日数差を出す（ADR-082 信号②）。

    disposition_gap = 負けの平均保有 − 勝ちの平均保有。正が大きいほど「勝ちを早く手仕舞い・負けを
    塩漬け」＝ディスポジション効果。どちらかが空なら gap は None（差を捏造しない）。
    """
    wins = [t for t in round_trips if t.get("is_win")]
    losses = [t for t in round_trips if not t.get("is_win")]

    def _avg_hold(trips: list[dict[str, Any]]) -> float | None:
        days = [d for t in trips if (d := t.get("holding_days")) is not None]
        return sum(days) / len(days) if days else None

    avg_win = _avg_hold(wins)
    avg_loss = _avg_hold(losses)
    gap = avg_loss - avg_win if (avg_win is not None and avg_loss is not None) else None
    return {
        "n_win": len(wins),
        "n_loss": len(losses),
        "avg_holding_days_win": avg_win,
        "avg_holding_days_loss": avg_loss,
        "disposition_gap": gap,
    }


# ---- ③ 繰り返す関心の集中 ---------------------------------------------------------------


def summarize_concentration(buys_by_bucket: dict[str, int]) -> list[dict[str, Any]]:
    """バケット別 buy 件数を降順に整形し share（構成比）を付ける（ADR-082 信号③）。

    同数はバケット名で安定ソート（決定的）。総数 0 は空リスト。バケットは sector17/テーマ等、
    呼び出し側（services）が組み立てた集計 dict をそのまま受ける（quant は割合化と整形だけ）。
    """
    total = sum(buys_by_bucket.values())
    if total <= 0:
        return []
    items = sorted(buys_by_bucket.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"bucket": b, "count": c, "share": c / total} for b, c in items]
