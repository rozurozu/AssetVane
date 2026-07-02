"""AI 提案の市場結果採点に使う純関数（ADR-077・提示ベースの銘柄選択スキル評価）。

設計の真実: docs/decisions.md ADR-077・tasks/hermes-transfer-2026-07-02.md（テーマ A）。

夜の分析AI・チャットが出した buy/sell 提案（proposals・ADR-052）と注目選別（notable_picks・
ADR-067）を、提案日の終値を起点に N 営業日後の実現（超過）リターンで採点する。ここは DB も today も
知らない純関数（終値系列 → 点対点リターン）。営業日カウントは「株価系列そのものの N 本先の終値」で
数える（別カレンダー非依存・休場を自然吸収＝quant/ml/train.py の系列カウントと同型・ADR-016）。

計算境界（ADR-014/016）: 実現/超過リターンと的中規則だけをここに置く。horizon（20/60）の値・価格源の
振り分け・DB 入出力は services/track_record.py が持つ（手法パラメータの置き場＝ADR-027）。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any


def _finite_positive(value: Any) -> float | None:
    """数値なら float、None/NaN/inf/0 以下は None を返す（価格として使えるかの判定）。"""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f) or f <= 0.0:
        return None
    return f


def _entry_index(prices: Sequence[dict[str, Any]], entry_date: str) -> int | None:
    """date が entry_date 以上の最初のバー index を返す（forward・ADR-077 決定③）。

    提案日が休場/未取得なら翌営業日のバーを起点に採る（遅延時に過去の古い終値へ誤アンカーせず、
    起点自体が無ければ pending に倒す）。系列は date 昇順前提。無ければ None。
    """
    for i, bar in enumerate(prices):
        if str(bar.get("date")) >= entry_date:
            return i
    return None


def _return_between(
    series: Sequence[dict[str, Any]], i: int, j: int, price_key: str
) -> float | None:
    """系列の index i→j の単純リターン（j/i - 1）を返す。端が価格として無効なら None。"""
    entry = _finite_positive(series[i].get(price_key))
    exit_ = _finite_positive(series[j].get(price_key))
    if entry is None or exit_ is None:
        return None
    return exit_ / entry - 1.0


def compute_horizon_outcome(
    prices: Sequence[dict[str, Any]],
    benchmark: Sequence[dict[str, Any]] | None,
    *,
    entry_date: str,
    horizon: int,
) -> dict[str, Any]:
    """提案の起点日から horizon 営業日後までの実現/超過リターンを系列カウントで採点する（ADR-077）。

    引数:
      prices: 対象銘柄の [{date, adj_close}]（date 昇順）。
      benchmark: 対ベンチ [{date, close}]（date 昇順）or None（絶対のみ）。
      entry_date: 起点日 'YYYY-MM-DD'（proposals.created_date / notable_picks.date）。
      horizon: 保有営業日数（系列 N 本先＝20/60）。

    採点規則:
      起点バー = date が entry_date 以上の最初のバー（forward・休場/未取得は翌営業日へ前進）。
      到達バー = 起点 index + horizon（未到達なら pending）。
      realized = 到達/起点 - 1（絶対）。excess = realized - ベンチの同一日 起点/到達の実現。
      ベンチが起点/到達日に無い・benchmark=None なら excess=None＋benchmark_fallback=True。

    返り値（DB 非依存の素の dict・数値を捏造しない＝ADR-014）:
      {status: 'pending'|'final', entry_priced_date, entry_price, as_of_date, exit_price,
       realized_return, excess_return, benchmark_fallback}。pending の欄は None。
    """
    pending: dict[str, Any] = {
        "status": "pending",
        "entry_priced_date": None,
        "entry_price": None,
        "as_of_date": None,
        "exit_price": None,
        "realized_return": None,
        "excess_return": None,
        "benchmark_fallback": False,
    }

    i = _entry_index(prices, entry_date)
    if i is None:
        return pending  # 起点バーがまだ無い（遅延で未取得 等）
    entry_price = _finite_positive(prices[i].get("adj_close"))
    if entry_price is None:
        return pending  # 起点バーはあるが価格が使えない（比率不能）

    entry_priced_date = str(prices[i].get("date"))
    j = i + horizon
    if j >= len(prices):
        # 到達バーがまだ無い＝horizon 未経過。起点情報だけ持って pending（翌晩以降 final へ）。
        return {**pending, "entry_priced_date": entry_priced_date, "entry_price": entry_price}

    exit_price = _finite_positive(prices[j].get("adj_close"))
    if exit_price is None:
        return {**pending, "entry_priced_date": entry_priced_date, "entry_price": entry_price}

    as_of_date = str(prices[j].get("date"))
    realized = exit_price / entry_price - 1.0

    excess, fallback = _excess_return(benchmark, entry_priced_date, as_of_date, realized)

    return {
        "status": "final",
        "entry_priced_date": entry_priced_date,
        "entry_price": entry_price,
        "as_of_date": as_of_date,
        "exit_price": exit_price,
        "realized_return": realized,
        "excess_return": excess,
        "benchmark_fallback": fallback,
    }


def _excess_return(
    benchmark: Sequence[dict[str, Any]] | None,
    entry_date: str,
    exit_date: str,
    realized: float,
) -> tuple[float | None, bool]:
    """ベンチの entry_date/exit_date の close から超過リターンを返す（(excess, fallback)）。

    ベンチ未提供・当該日欠測・起点 close が無効なら excess=None・fallback=True（絶対で判定の合図）。
    """
    if not benchmark:
        return None, True
    closes = {str(b.get("date")): b.get("close") for b in benchmark}
    b_entry = _finite_positive(closes.get(entry_date))
    b_exit = _finite_positive(closes.get(exit_date))
    if b_entry is None or b_exit is None:
        return None, True
    bench_return = b_exit / b_entry - 1.0
    return realized - bench_return, False


def classify_hit(
    kind: str, excess_return: float | None, realized_return: float | None
) -> bool | None:
    """方向性提案の的中を返す（ADR-077）。buy→リターン>0、sell→リターン<0、notable→None。

    判定値は excess を優先し、excess=None（ベンチ欠測）なら realized にフォールバックする。
    どちらも None（pending）なら None。方向性の無い notable は常に None（リターンのみ記録）。
    """
    if kind == "notable":
        return None
    value = excess_return if excess_return is not None else realized_return
    if value is None:
        return None
    if kind == "buy":
        return value > 0.0
    if kind == "sell":
        return value < 0.0
    return None
