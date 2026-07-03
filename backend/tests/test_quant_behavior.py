"""quant.behavior の行動信号 純関数を担保する（ADR-082・投資家プロファイル・★4 自己改善ループ）。

投資家の「行動の癖」を台帳から計算する純関数（DB も today も要らない＝testing-strategy）:
  ①summarize_sell_regret … 売った後に上がった率（手仕舞い早すぎ）を final outcome 群から集計
  ②match_round_trips / summarize_disposition … FIFO 往復突合＋勝ち急ぎ・損塩漬けの保有日数差
  ③summarize_concentration … buy 集中（セクター/テーマ）の降順整形
数値は捏造しない・データ不足は安全な既定（None/空）を返す（ADR-014/016・backend-service-quant-pattern）。
"""

from __future__ import annotations

import pytest

from app.quant.behavior import (
    match_round_trips,
    summarize_concentration,
    summarize_disposition,
    summarize_sell_regret,
)

# ---- ① 手仕舞いの帰結（summarize_sell_regret） --------------------------------------------


def _final(realized: float | None, excess: float | None) -> dict[str, object]:
    return {"status": "final", "realized_return": realized, "excess_return": excess}


def _pending() -> dict[str, object]:
    return {"status": "pending", "realized_return": None, "excess_return": None}


def test_sell_regret_basic_rates():
    """final のうち realized>0 が recover_rate、excess>0 が excess_recover_rate に一致。"""
    outcomes = [
        _final(0.10, 0.05),  # 上がった・ベンチ超過
        _final(0.02, -0.01),  # 上がった・ベンチ未満
        _final(-0.08, -0.10),  # 下がった（売り正解）
        _pending(),  # 未経過は母集団外
    ]
    r = summarize_sell_regret(outcomes)
    assert r["n_final"] == 3
    assert r["n_pending"] == 1
    assert r["recover_rate"] == pytest.approx(2 / 3)  # realized>0 が 2/3
    assert r["excess_recover_rate"] == pytest.approx(1 / 3)  # excess>0 が 1/3
    assert r["avg_realized_return"] == pytest.approx((0.10 + 0.02 - 0.08) / 3)
    assert r["n_excess"] == 3


def test_sell_regret_excess_ignores_none():
    """excess=None（ベンチ欠測）の final は excess 系の母数から除く（realized 系には残す）。"""
    outcomes = [_final(0.10, None), _final(0.20, 0.15)]
    r = summarize_sell_regret(outcomes)
    assert r["n_final"] == 2
    assert r["n_excess"] == 1  # excess ありは 1 件だけ
    assert r["excess_recover_rate"] == pytest.approx(1.0)
    assert r["avg_realized_return"] == pytest.approx(0.15)


def test_sell_regret_empty_is_safe():
    """final ゼロなら率・平均は None（例外で落ちない・数値を捏造しない）。"""
    r = summarize_sell_regret([_pending(), _pending()])
    assert r["n_final"] == 0
    assert r["recover_rate"] is None
    assert r["avg_realized_return"] is None
    assert r["excess_recover_rate"] is None


# ---- ② ディスポジション効果（match_round_trips / summarize_disposition） --------------------


def _tx(code: str, side: str, shares: float, price: float, date: str) -> dict[str, object]:
    return {"code": code, "side": side, "shares": shares, "price": price, "traded_at": date}


def test_round_trips_fifo_basic():
    """FIFO で buy→sell を突合し holding_days・is_win・pnl を出す（勝ちトレード）。"""
    txns = [
        _tx("7203", "buy", 100, 1000.0, "2026-01-01"),
        _tx("7203", "sell", 100, 1200.0, "2026-01-11"),
    ]
    trips = match_round_trips(txns)
    assert len(trips) == 1
    t = trips[0]
    assert t["code"] == "7203"
    assert t["shares"] == pytest.approx(100)
    assert t["is_win"] is True
    assert t["holding_days"] == 10  # 暦日差
    assert t["pnl"] == pytest.approx((1200.0 - 1000.0) * 100)


def test_round_trips_fifo_partial_and_multilot():
    """複数ロットを FIFO で分割消費し、ロットごとに勝ち負け・保有日数を分ける。"""
    txns = [
        _tx("6758", "buy", 50, 1000.0, "2026-01-01"),  # ロットA（安い）
        _tx("6758", "buy", 50, 2000.0, "2026-02-01"),  # ロットB（高い）
        _tx("6758", "sell", 75, 1500.0, "2026-03-03"),  # A 全部＋B 25 株を消費
    ]
    trips = match_round_trips(txns)
    assert len(trips) == 2
    a, b = trips
    assert a["shares"] == pytest.approx(50) and a["buy_price"] == pytest.approx(1000.0)
    assert a["is_win"] is True  # 1500 > 1000
    assert b["shares"] == pytest.approx(25) and b["buy_price"] == pytest.approx(2000.0)
    assert b["is_win"] is False  # 1500 < 2000


def test_round_trips_skips_unmatched_sell():
    """買いロットを超える売り（台帳欠落/空売り）は未対応分を安全に捨てる（例外で落ちない）。"""
    txns = [
        _tx("9984", "buy", 10, 100.0, "2026-01-01"),
        _tx("9984", "sell", 30, 120.0, "2026-01-05"),  # 20 株は対応買いなし
    ]
    trips = match_round_trips(txns)
    assert len(trips) == 1
    assert trips[0]["shares"] == pytest.approx(10)


def test_disposition_gap_detects_hold_losers():
    """勝ちを早く・負けを長く持つとき disposition_gap（負けの平均保有−勝ち）が正になる。"""
    trips = [
        {"is_win": True, "holding_days": 10},
        {"is_win": True, "holding_days": 20},
        {"is_win": False, "holding_days": 80},
        {"is_win": False, "holding_days": 100},
    ]
    d = summarize_disposition(trips)
    assert d["n_win"] == 2 and d["n_loss"] == 2
    assert d["avg_holding_days_win"] == pytest.approx(15)
    assert d["avg_holding_days_loss"] == pytest.approx(90)
    assert d["disposition_gap"] == pytest.approx(75)  # 90 - 15


def test_disposition_empty_is_safe():
    d = summarize_disposition([])
    assert d["n_win"] == 0 and d["n_loss"] == 0
    assert d["avg_holding_days_win"] is None
    assert d["disposition_gap"] is None


# ---- ③ 繰り返す関心の集中（summarize_concentration） --------------------------------------


def test_concentration_sorted_with_share():
    """バケット別 count を降順に整形し share（構成比）を付ける。"""
    rows = summarize_concentration({"半導体": 8, "銀行": 1, "自動車": 3})
    assert [r["bucket"] for r in rows] == ["半導体", "自動車", "銀行"]
    assert rows[0]["count"] == 8
    assert rows[0]["share"] == pytest.approx(8 / 12)


def test_concentration_ties_break_by_name_and_empty():
    """同数はバケット名で安定ソート。空入力は空リスト。"""
    rows = summarize_concentration({"b": 2, "a": 2})
    assert [r["bucket"] for r in rows] == ["a", "b"]
    assert summarize_concentration({}) == []
