"""valuation Tool（get_valuation / screen_valuation）の handler 検証（ADR-048）。

一時 SQLite・ネットに出ない（testing-strategy）。検証の芯:
- 市場が契約として明示される（market="JP" / currency="JPY"）。
- Tool は事実だけを返し **verdict（割安/割高の判定）を持たない**（ADR-014）。
- screen_valuation が criteria で絞り、各候補に指標が載る。
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.advisor.tools import handlers
from app.db import repo
from app.db.engine import get_engine
from app.services import valuation as valsvc

# verdict（判定）を Tool が漏らしていないかの監査キー（ADR-014）。
_VERDICT_KEYS = {"verdict", "is_cheap", "is_undervalued", "判定", "割安", "rating"}


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _seed(temp_db) -> None:
    """1 銘柄ぶんの stocks/quotes/financials を入れ、valuation_snapshots を焼く。"""
    repo.upsert_stocks(
        [
            {
                "code": "72030",
                "company_name": "会社72030",
                "sector33_code": "3700",
                "sector17_code": "6",
                "market_code": "0111",
                "is_etf": 0,
                "updated_at": "2026-06-04T00:00:00+00:00",
            }
        ]
    )
    repo.upsert_daily_quotes(
        [
            {
                "code": "72030",
                "date": "2026-06-03",
                "open": 2500.0,
                "high": 2500.0,
                "low": 2500.0,
                "close": 2500.0,
                "volume": 1.0,
                "adj_close": 2500.0,
            }
        ]
    )
    repo.upsert_financials(
        [
            {
                "code": "72030",
                "disclosed_date": "2025-05-08",
                "fiscal_period": "FY",
                "net_sales": 1200.0,
                "operating_profit": 120.0,
                "profit": 72.0,
                "eps": 359.56,
                "bps": 2753.09,
                "dividend_per_share": 90.0,
                "shares_outstanding": 1_000_000.0,
                "treasury_shares": 0.0,
            }
        ]
    )
    with get_engine().connect() as conn:
        rows = valsvc.build_valuation_snapshots(conn)
    repo.upsert_valuation_snapshots(rows)


def test_get_valuation_returns_facts_with_market_and_no_verdict(temp_db) -> None:
    _seed(temp_db)
    out = _run(handlers.handle_get_valuation({"code": "72030"}))

    assert out["found"] is True
    assert out["market"] == "JP"
    assert out["currency"] == "JPY"
    assert out["as_of"] == "2026-06-03"
    # 事実が載る（PER/PBR/ROE）
    assert abs(out["per"] - 2500.0 / 359.56) < 1e-9
    assert abs(out["pbr"] - 2500.0 / 2753.09) < 1e-9
    assert abs(out["roe"] - 359.56 / 2753.09) < 1e-9
    # 判定（verdict）は Tool が持たない（解釈は LLM・ADR-014）
    assert _VERDICT_KEYS.isdisjoint(out.keys())


def test_get_valuation_not_found(temp_db) -> None:
    _seed(temp_db)
    out = _run(handlers.handle_get_valuation({"code": "99999"}))
    assert out["found"] is False
    assert out["market"] == "JP"
    assert out["currency"] == "JPY"


def test_get_valuation_relays_receivables_inventory_quality(temp_db) -> None:
    """#2 売掛/在庫の質（DSO/DIO・YoY）が calc_receivables_inventory の UPDATE 後に中継される。"""
    _seed(temp_db)
    with get_engine().begin() as conn:
        repo.update_valuation_receivables_inventory(
            conn,
            "72030",
            {
                "receivables_turnover_days": 49.77,
                "inventory_turnover_days": 123.25,
                "receivables_growth_yoy": 0.5,
                "inventory_growth_yoy": 0.3,
            },
        )
    out = _run(handlers.handle_get_valuation({"code": "72030"}))
    assert abs(out["receivables_growth_yoy"] - 0.5) < 1e-9
    assert abs(out["inventory_growth_yoy"] - 0.3) < 1e-9
    assert abs(out["receivables_turnover_days"] - 49.77) < 1e-9
    assert _VERDICT_KEYS.isdisjoint(out.keys())  # verdict は持たない（解釈は LLM）


def test_get_valuation_relays_forecast_guidance(temp_db) -> None:
    """会社予想 beat/miss・上方/下方修正を中継し、verdict は持たない（ADR-063 #4）。"""
    repo.upsert_stocks(
        [
            {
                "code": "72030",
                "company_name": "会社72030",
                "sector33_code": "3700",
                "sector17_code": "6",
                "market_code": "0111",
                "is_etf": 0,
                "updated_at": "2026-06-04T00:00:00+00:00",
            }
        ]
    )
    repo.upsert_daily_quotes(
        [
            {
                "code": "72030",
                "date": "2026-06-03",
                "open": 2500.0,
                "high": 2500.0,
                "low": 2500.0,
                "close": 2500.0,
                "volume": 1.0,
                "adj_close": 2500.0,
            }
        ]
    )

    def _fin(date: str, period: str, **kw) -> dict:
        base = {
            "code": "72030",
            "disclosed_date": date,
            "fiscal_period": period,
            "net_sales": None,
            "operating_profit": None,
            "profit": None,
            "eps": None,
            "bps": None,
            "dividend_per_share": None,
            "shares_outstanding": None,
            "treasury_shares": None,
            "forecast_net_sales": None,
            "forecast_operating_profit": None,
            "forecast_profit": None,
            "forecast_eps": None,
        }
        base.update(kw)
        return base

    repo.upsert_financials(
        [
            _fin("2025-02-05", "3Q", forecast_operating_profit=4.70e12),  # 最終予想
            _fin("2025-05-08", "FY", operating_profit=4.80e12, eps=359.56, bps=2753.09),
            _fin("2025-08-07", "1Q", forecast_operating_profit=3.20e12),
            _fin("2026-02-06", "3Q", forecast_operating_profit=3.80e12),  # 直近修正
        ]
    )
    with get_engine().connect() as conn:
        snaps = valsvc.build_valuation_snapshots(conn)
    repo.upsert_valuation_snapshots(snaps)

    out = _run(handlers.handle_get_valuation({"code": "72030"}))
    assert out["found"] is True
    assert abs(out["op_forecast_achievement"] - 4.80e12 / 4.70e12) < 1e-9  # beat
    assert abs(out["op_forecast_revision"] - (3.80e12 / 3.20e12 - 1)) < 1e-9  # 上方修正
    # 予想を出さない純利益は None で中継（捏造しない）
    assert out["profit_forecast_achievement"] is None
    # verdict（判定）は持たない（ADR-014）
    assert _VERDICT_KEYS.isdisjoint(out.keys())


def test_get_valuation_relays_last_restatement_at(temp_db) -> None:
    """訂正有報があれば last_restatement_at（事実=最新提出日）を中継し、無ければ None（B-2）。"""
    _seed(temp_db)
    # 訂正なし → None（事実=日付のみ・recency 判定は LLM・ADR-014）
    out = _run(handlers.handle_get_valuation({"code": "72030"}))
    assert out["found"] is True
    assert out["last_restatement_at"] is None
    # 訂正を記録 → 最新提出日を中継し、verdict は持たない
    repo.record_edinet_restatement(
        {"doc_id": "X1", "code": "72030", "disclosed_date": "2026-05-20"}
    )
    out2 = _run(handlers.handle_get_valuation({"code": "72030"}))
    assert out2["last_restatement_at"] == "2026-05-20"
    assert _VERDICT_KEYS.isdisjoint(out2.keys())
    # valuation 未焼成（found=False）でも訂正は独立に中継する
    repo.record_edinet_restatement(
        {"doc_id": "X2", "code": "99999", "disclosed_date": "2026-04-01"}
    )
    out3 = _run(handlers.handle_get_valuation({"code": "99999"}))
    assert out3["found"] is False
    assert out3["last_restatement_at"] == "2026-04-01"


def test_screen_valuation_filters_and_labels_market(temp_db) -> None:
    _seed(temp_db)
    # per はおよそ 6.95 倍 → per_max=15 で 1 件ヒット
    out = _run(handlers.handle_screen_valuation({"per_max": 15.0}))
    assert out["market"] == "JP"
    assert out["currency"] == "JPY"
    assert out["count"] == 1
    item = out["items"][0]
    assert item["code"] == "72030"
    assert "per" in item and "roe" in item
    # しきい値を厳しくすると 0 件（破壊的ゲートはコードに無い＝AI が criteria を渡す）
    out2 = _run(handlers.handle_screen_valuation({"per_max": 1.0}))
    assert out2["count"] == 0
