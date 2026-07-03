"""投資家プロファイル蒸留の素材構築サービスを担保する（ADR-082・テーマ C・★4 自己改善ループ）。

一時 SQLite に台帳（portfolio/取引/価格/指数）を seed し、①活動量ゲート（count_new_sells）と
カーソル前進（advance_cursor）が単調に効くこと ②build_behavior_material が手仕舞い・ディスポ・
関心集中の 3 信号を quant に委ねて組むこと ③min_samples 足切りが format に効くことを検証する
（testing-strategy・数値計算は quant/behavior に委ね services は下ごしらえだけ＝ADR-014）。
"""

from __future__ import annotations

import pytest

from app.db import schema
from app.db.engine import get_engine
from app.services import investor_behavior as ib


def _seed_quote(conn, code: str, bars: list[tuple[str, float]]) -> None:
    for d, adj in bars:
        conn.execute(
            schema.daily_quotes.insert().values(code=code, date=d, adj_close=adj, close=adj)
        )


def _seed_index(conn, symbol: str, bars: list[tuple[str, float]]) -> None:
    for d, close in bars:
        conn.execute(schema.index_quotes.insert().values(symbol=symbol, date=d, close=close))


def _tx(conn, pid, code, side, shares, price, date) -> None:
    conn.execute(
        schema.transactions.insert().values(
            portfolio_id=pid, code=code, side=side, shares=shares, price=price, traded_at=date
        )
    )


def _new_portfolio(conn) -> int:
    pk = conn.execute(schema.portfolios.insert().values(name="main")).inserted_primary_key
    assert pk is not None
    return int(pk[0])


def _seed_ledger(conn) -> int:
    """1 portfolio・2 銘柄・買い 2 件＋売り 1 件・価格/指数を仕込み、portfolio_id を返す。"""
    pid = _new_portfolio(conn)
    conn.execute(
        schema.stocks.insert().values(code="7203", company_name="トヨタ", sector17_code="6")
    )
    conn.execute(
        schema.stocks.insert().values(code="6758", company_name="ソニー", sector17_code="6")
    )
    _seed_quote(conn, "7203", [("2026-01-05", 100.0), ("2026-01-06", 110.0)])
    _seed_index(conn, "^TPX", [("2026-01-05", 200.0), ("2026-01-06", 202.0)])
    _tx(conn, pid, "7203", "buy", 100, 90.0, "2026-01-01")
    _tx(conn, pid, "6758", "buy", 50, 100.0, "2026-01-02")
    _tx(conn, pid, "7203", "sell", 100, 95.0, "2026-01-05")
    return pid


def test_activity_gate_and_cursor_monotonic(temp_db):
    """未蒸留は SELL 全件が新着・advance 後は 0 件（同じ SELL を二度数えない・ADR-082）。"""
    with get_engine().begin() as conn:
        _seed_ledger(conn)

    with get_engine().connect() as conn:
        assert ib.profiler_cursor(conn) is None
        assert ib.count_new_sells(conn) == 1  # SELL は 1 件

    with get_engine().begin() as conn:
        latest = ib.advance_cursor(conn)
    assert latest == "2026-01-05"

    with get_engine().connect() as conn:
        assert ib.profiler_cursor(conn) == "2026-01-05"
        assert ib.count_new_sells(conn) == 0  # カーソル超の新規 SELL は無い


def test_advance_cursor_noop_without_sells(temp_db):
    """SELL が無ければカーソルは据え置き（None を返す・count も 0）。"""
    with get_engine().begin() as conn:
        pid = _new_portfolio(conn)
        conn.execute(schema.stocks.insert().values(code="7203", company_name="トヨタ"))
        _tx(conn, pid, "7203", "buy", 100, 90.0, "2026-01-01")
    with get_engine().begin() as conn:
        assert ib.advance_cursor(conn) is None
    with get_engine().connect() as conn:
        assert ib.count_new_sells(conn) == 0


def test_build_material_three_signals(temp_db, monkeypatch):
    """3 信号（手仕舞い・ディスポ・関心集中）が quant 経由で組める（horizon は (1,) に縮める）。"""
    monkeypatch.setattr(ib, "_HORIZONS", (1,))
    with get_engine().begin() as conn:
        _seed_ledger(conn)

    with get_engine().connect() as conn:
        material = ib.build_behavior_material(conn, min_samples=1)

    sr = material["sell_regret"]
    assert sr["n_final"] == 1  # SELL 1 件 × horizon 1 本 → final 1
    assert sr["recover_rate"] == pytest.approx(1.0)  # 100→110 で上がった（手仕舞い早すぎ）
    assert sr["excess_recover_rate"] == pytest.approx(1.0)  # ベンチ超過でも上昇

    dp = material["disposition"]
    assert dp["n_win"] == 1 and dp["n_loss"] == 0  # 90→95 の勝ち往復 1 件

    conc = material["concentration"]
    assert len(conc) == 1  # 2 買いとも sector17='6' の 1 バケットに集中
    assert conc[0]["count"] == 2
    assert material["min_samples"] == 1


def test_format_applies_min_samples_floor(temp_db, monkeypatch):
    """min_samples を超える信号だけ「傾向」として断定し、未満は断定不可と明示する。"""
    monkeypatch.setattr(ib, "_HORIZONS", (1,))
    with get_engine().begin() as conn:
        _seed_ledger(conn)
    with get_engine().connect() as conn:
        material = ib.build_behavior_material(conn, min_samples=5)  # 5 件には届かない

    text = ib.format_behavior_material_for_prompt(material)
    assert "手仕舞いの帰結" in text
    assert "断定不可" in text  # サンプル 1 < 閾値 5 なので断定しない
