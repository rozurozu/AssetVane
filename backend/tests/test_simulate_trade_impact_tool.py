"""simulate_trade_impact ツール（#4・ADR-085）の結合テスト。

一時 SQLite に portfolios / stocks / daily_quotes / holdings / cash をスタブし、
pro-forma のウェイト・集中度・相関・リスク寄与が返ること、US/未知は found:False で落ちないこと、
返り値が JSON-safe であることを検証する（testing-strategy・ネットに出ない）。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import numpy as np
import pandas as pd

from app.advisor.tools import handlers
from app.advisor.tools.registry import openai_tools
from app.db import repo
from app.db.engine import get_engine


def _stock(code: str, name: str, sector33: str = "3700") -> dict[str, Any]:
    return {
        "code": code,
        "company_name": name,
        "sector33_code": sector33,
        "sector17_code": "6",
        "market_code": "0111",
        "is_etf": 0,
        "updated_at": "2026-01-01T00:00:00+00:00",
    }


def _seed_quotes(code: str, closes: list[float], start: str = "2026-01-01") -> None:
    dates = pd.date_range(start, periods=len(closes), freq="B")
    rows = [
        {
            "code": code,
            "date": d.strftime("%Y-%m-%d"),
            "open": c,
            "high": c,
            "low": c,
            "close": c,
            "volume": 1000.0,
            "adj_close": c,
        }
        for d, c in zip(dates, closes, strict=True)
    ]
    repo.upsert_daily_quotes(rows)


def _series(seed: int, n: int = 60, mu: float = 0.0004, sigma: float = 0.01) -> list[float]:
    rng = np.random.default_rng(seed)
    return list(np.cumprod(1.0 + rng.normal(mu, sigma, n)) * 1000.0)


def _seed_portfolio_with_two_holdings() -> int:
    """A/B を各 100 株保有・現金ありのポートフォリオを作り portfolio_id を返す。"""
    from app.db.schema import holdings, portfolios

    repo.upsert_stocks([_stock("72030", "トヨタ", "3700"), _stock("67580", "ソニー", "3600")])
    _seed_quotes("72030", _series(1))
    _seed_quotes("67580", _series(2))
    with get_engine().begin() as conn:
        pid = conn.execute(
            portfolios.insert().values(name="メイン", created_at="2026-01-01T00:00:00+00:00")
        ).inserted_primary_key[0]
        for code in ("72030", "67580"):
            conn.execute(
                holdings.insert().values(portfolio_id=pid, code=code, shares=100.0, avg_cost=900.0)
            )
    repo.upsert_cash(500000.0)
    return int(pid)


def _call(args: dict[str, Any]) -> dict[str, Any]:
    return asyncio.run(handlers.handle_simulate_trade_impact(args))


def test_buy_existing_increases_weight(temp_db: None) -> None:
    """既存銘柄を買い増すと proforma_weight が current_weight より増え、集中度も上がる。"""
    pid = _seed_portfolio_with_two_holdings()
    out = _call({"action": "buy", "code": "72030", "amount_jpy": 300000.0, "portfolio_id": pid})

    assert out["found"] is True
    assert out["market"] == "JP"
    pos = out["position"]
    assert pos["proforma_weight"] > pos["current_weight"]

    def _max_pos(devs: list[dict[str, Any]]) -> dict[str, Any]:
        return next(d for d in devs if d["kind"] == "max_position")

    cur = _max_pos(out["concentration"]["current"])
    pf = _max_pos(out["concentration"]["proforma"])
    assert pf["current"] > cur["current"]  # 買い増しで最大ウェイトが上昇


def test_buy_new_stock_fills_correlation_and_risk(temp_db: None) -> None:
    """未保有の履歴あり銘柄を買うと相関・リスク寄与が埋まる。"""
    pid = _seed_portfolio_with_two_holdings()
    repo.upsert_stocks([_stock("99840", "候補", "3700")])
    _seed_quotes("99840", _series(9))

    out = _call({"action": "buy", "code": "99840", "amount_jpy": 200000.0, "portfolio_id": pid})
    assert out["found"] is True
    assert len(out["correlation_to_holdings"]) >= 1
    assert out["risk_contribution"]["percent"] is not None
    assert out["portfolio_volatility"]["proforma_annual"] is not None


def test_sell_over_holding_clamps(temp_db: None) -> None:
    """保有評価額を超える売却は全部売却に丸め、proforma_value_jpy=0・note を残す。"""
    pid = _seed_portfolio_with_two_holdings()
    out = _call(
        {"action": "sell", "code": "72030", "amount_jpy": 9_999_999_999.0, "portfolio_id": pid}
    )

    assert out["found"] is True
    assert out["position"]["proforma_value_jpy"] == 0.0
    assert any("丸め" in n for n in out["notes"])


def test_unknown_code_found_false(temp_db: None) -> None:
    """未知コードは found:False で例外を出さずに返る（market=None）。"""
    pid = _seed_portfolio_with_two_holdings()
    out = _call({"action": "buy", "code": "99999", "amount_jpy": 100000.0, "portfolio_id": pid})

    assert out["found"] is False
    assert out["market"] is None
    assert "error" not in out


def test_result_is_json_safe(temp_db: None) -> None:
    """返り値がそのまま json.dumps できる（Decimal/numpy を漏らさない・handler 契約）。"""
    pid = _seed_portfolio_with_two_holdings()
    out = _call({"action": "buy", "code": "72030", "amount_jpy": 100000.0, "portfolio_id": pid})
    json.dumps(out)  # 例外が出なければ JSON-safe
    assert "is_delayed" in out  # handler が as_of から付与（ADR-071）


def test_invalid_amount_returns_error(temp_db: None) -> None:
    """amount_jpy<=0 は検証で弾かれ {error} に倒れる（dispatch を落とさない）。"""
    pid = _seed_portfolio_with_two_holdings()
    out = _call({"action": "buy", "code": "72030", "amount_jpy": -5.0, "portfolio_id": pid})
    assert "error" in out


def test_registry_exposes_tool_at_phase2() -> None:
    """simulate_trade_impact は Phase 2 で露出し、Phase 1 では見えない。"""
    names_p2 = {t["function"]["name"] for t in openai_tools(2)}
    names_p1 = {t["function"]["name"] for t in openai_tools(1)}
    assert "simulate_trade_impact" in names_p2
    assert "simulate_trade_impact" not in names_p1
