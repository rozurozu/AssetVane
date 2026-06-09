"""fetch_us_fundamentals の巡回（古い順＋夜天井）と fetch_meta 前進の検証（ADR-033/018）。

担保: list_us_symbols_for_fundamentals が「未取得最優先→古い順」で limit 件返すこと・ジョブが
各銘柄の fetch_meta['us_fundamentals:<symbol>'] を前進させること・YoY 率など us_stocks に列の無い
キーを書き込まず財務素だけ partial update すること。fake adapter で実 HTTP に出ない
（testing-strategy）。
"""

from __future__ import annotations

from typing import Any

from app.batch.jobs import fetch_us_fundamentals
from app.config import settings
from app.db import repo
from app.db.engine import get_engine


class _FakeAdapter:
    """fetch_fundamentals だけ持つ fake（YoY 率も返す＝us_stocks に列の無いキーを混ぜて検証）。"""

    def __init__(self, by_symbol: dict[str, dict[str, Any]]) -> None:
        self._by_symbol = by_symbol
        self.calls: list[str] = []

    def fetch_fundamentals(self, symbol: str) -> dict[str, Any]:
        self.calls.append(symbol)
        return self._by_symbol[symbol]


def _info(name: str, **kw) -> dict[str, Any]:
    base = {
        "company_name": name,
        "gics_sector": "Technology",
        "industry": "sub",
        "eps": 5.0,
        "bps": 10.0,
        "shares_net": 100.0,
        "dividend_per_share": 1.0,
        "net_sales": 200.0,
        "operating_profit": 30.0,
        "profit": 20.0,
        "fin_disclosed_date": None,
        # us_stocks に列の無いキー（捨てられるべき・ADR-055）。
        "revenue_growth_yoy": 0.1,
        "earnings_growth_yoy": 0.2,
    }
    base.update(kw)
    return base


def test_oldest_first_with_nightly_cap(temp_db, monkeypatch) -> None:
    """未取得最優先→古い順で天井まで拾い、財務素のみ partial update・fetch_meta が前進する。"""
    repo.upsert_us_stocks(
        [
            {"symbol": "AAA", "company_name": "A", "is_etf": 0, "updated_at": "t"},
            {"symbol": "BBB", "company_name": "B", "is_etf": 0, "updated_at": "t"},
            {"symbol": "CCC", "company_name": "C", "is_etf": 0, "updated_at": "t"},
        ]
    )
    # BBB は最近取得済み・CCC は古い取得済み・AAA は未取得（カーソル無し）。
    repo.upsert_fetch_meta("us_fundamentals:BBB", "2026-06-08")
    repo.upsert_fetch_meta("us_fundamentals:CCC", "2026-01-01")

    # 夜天井を 2 に絞る → 未取得 AAA → 古い CCC の 2 件（BBB は後回し）。
    monkeypatch.setattr(settings, "us_fundamentals_nightly_max", 2)
    with get_engine().connect() as conn:
        targets = repo.list_us_symbols_for_fundamentals(conn, settings.us_fundamentals_nightly_max)
    assert targets == ["AAA", "CCC"]

    fake = _FakeAdapter({"AAA": _info("Apple A"), "CCC": _info("Cee C")})
    result = fetch_us_fundamentals.run(adapter=fake)  # type: ignore[arg-type]
    assert result.ok is True
    assert result.rows == 2
    assert fake.calls == ["AAA", "CCC"]

    with get_engine().connect() as conn:
        aaa = repo.get_us_stock(conn, "AAA")
        meta = repo.get_fetch_meta(conn, "us_fundamentals:AAA")
    # 財務素は焼け、universe 側の company_name は `.info` の値で更新（partial update が壊れない）。
    assert aaa["eps"] == 5.0
    assert aaa["gics_sector"] == "Technology"
    assert aaa["company_name"] == "Apple A"
    # YoY 中継率（`.info` 提供の実値）が us_stocks に焼かれている（ADR-055・統括判断で活かす方針）。
    assert aaa["revenue_growth_yoy"] == 0.1
    assert aaa["earnings_growth_yoy"] == 0.2
    # fetch_meta が前進（次回は AAA が後回しになる）。
    assert meta is not None and meta["last_fetched_date"]


def test_partial_failure_keeps_going(temp_db, monkeypatch) -> None:
    """1 銘柄が例外でも後続を止めず ok=False・失敗銘柄は last_attempt_ok=0 を記録（ADR-018）。"""
    repo.upsert_us_stocks(
        [
            {"symbol": "AAA", "company_name": "A", "is_etf": 0, "updated_at": "t"},
            {"symbol": "BAD", "company_name": "Bad", "is_etf": 0, "updated_at": "t"},
        ]
    )
    monkeypatch.setattr(settings, "us_fundamentals_nightly_max", 10)

    class _PartlyFailing(_FakeAdapter):
        def fetch_fundamentals(self, symbol: str):
            self.calls.append(symbol)
            if symbol == "BAD":
                raise RuntimeError("boom")
            return self._by_symbol[symbol]

    fake = _PartlyFailing({"AAA": _info("Apple A")})
    result = fetch_us_fundamentals.run(adapter=fake)  # type: ignore[arg-type]
    assert result.ok is False  # 失敗 1 件あり
    assert "AAA" in fake.calls and "BAD" in fake.calls  # 後続を止めていない

    with get_engine().connect() as conn:
        bad_meta = repo.get_fetch_meta(conn, "us_fundamentals:BAD")
        aaa = repo.get_us_stock(conn, "AAA")
    assert bad_meta is not None and bad_meta["last_attempt_ok"] == 0  # 失敗を記録
    assert aaa["eps"] == 5.0  # 成功した銘柄は焼けている
