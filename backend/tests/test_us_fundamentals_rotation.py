"""fetch_us_fundamentals の巡回（古い順＋夜天井）と fetch_meta 前進の検証（ADR-033/018）。

担保: list_us_symbols_for_fundamentals が「未取得最優先→古い順」で limit 件返すこと・ジョブが
各銘柄の fetch_meta['us_fundamentals:<symbol>'] を前進させること・YoY 率など us_stocks に列の無い
キーを書き込まず財務素だけ partial update すること・business_summary が非空のときだけ
company_descriptions へ相乗り保存されること（ADR-050 段階A・捏造しない＝ADR-014）・空 `.info` で
アダプタが raise した銘柄は partial UPSERT もローテカーソル前進もしないこと
（tasks/review-2026-06-12.md C-4）。fake adapter で実 HTTP に出ない（testing-strategy）。
"""

from __future__ import annotations

from typing import Any

from app.batch import state
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


def test_empty_info_raise_keeps_existing_values_and_cursor(temp_db, monkeypatch) -> None:
    """空 `.info` でアダプタが raise した銘柄は partial UPSERT もローテカーソル前進もしない（C-4）。

    実アダプタ（YahooUsEquitySource）に空 `.info` を注入して raise 経路を実際に通し、
    過去に焼けた財務値（eps/net_sales）が NULL で潰れないこと・
    fetch_meta['us_fundamentals:<symbol>'] の last_fetched_date が据え置かれること
    （古い順巡回で翌晩も再訪される）・ok=False（ADR-018）を担保する。
    """
    from app.adapters.us_equity import UsEquityAdapter, YahooUsEquitySource

    repo.upsert_us_stocks([{"symbol": "AAA", "company_name": "A", "is_etf": 0, "updated_at": "t"}])
    # 過去の巡回で焼けた財務値とローテカーソルを再現する。
    repo.upsert_us_stocks([{"symbol": "AAA", "eps": 5.0, "net_sales": 200.0}])
    repo.upsert_fetch_meta("us_fundamentals:AAA", "2026-01-01")
    monkeypatch.setattr(settings, "us_fundamentals_nightly_max", 10)

    # 空 `.info`（bot 検知/レート制限相当）→ アダプタが UsEquityAdapterError を raise する。
    adapter = UsEquityAdapter(sources=[YahooUsEquitySource(fetch_info=lambda _s: {})])
    result = fetch_us_fundamentals.run(adapter=adapter)

    assert result.ok is False
    assert "AAA" in result.detail

    with get_engine().connect() as conn:
        aaa = repo.get_us_stock(conn, "AAA")
        meta = repo.get_fetch_meta(conn, "us_fundamentals:AAA")
    # 既存の財務値が NULL で潰れていない（partial UPSERT が走っていない）。
    assert aaa["eps"] == 5.0
    assert aaa["net_sales"] == 200.0
    # ローテカーソルは据え置き（前進しない）＋失敗が記録される。
    assert meta is not None
    assert meta["last_fetched_date"] == "2026-01-01"
    assert meta["last_attempt_ok"] == 0


def test_business_summary_upserted_to_company_descriptions(temp_db, monkeypatch) -> None:
    """business_summary 非空の銘柄だけ company_descriptions へ相乗り保存（ADR-050 段階A）。

    欠損/空文字列の銘柄は書かない（捏造しない＝ADR-014）。保存行は market='US'・
    source='yfinance'・disclosed_date/doc_id は NULL（US は provenance を持たない）。
    business_summary は us_stocks には流れない（_US_STOCKS_FUNDAMENTAL_COLS 外で捨てられる）。
    """
    repo.upsert_us_stocks(
        [
            {"symbol": "AAA", "company_name": "A", "is_etf": 0, "updated_at": "t"},
            {"symbol": "BBB", "company_name": "B", "is_etf": 0, "updated_at": "t"},
            {"symbol": "CCC", "company_name": "C", "is_etf": 0, "updated_at": "t"},
        ]
    )
    monkeypatch.setattr(settings, "us_fundamentals_nightly_max", 10)

    fake = _FakeAdapter(
        {
            "AAA": _info("Apple A", business_summary="Apple A designs smartphones."),
            "BBB": _info("Bee B"),  # business_summary 欠損 → 書かれない
            "CCC": _info("Cee C", business_summary="   "),  # 空白のみ → 書かれない
        }
    )
    result = fetch_us_fundamentals.run(adapter=fake)  # type: ignore[arg-type]
    assert result.ok is True
    assert result.rows == 3

    with get_engine().connect() as conn:
        aaa_desc = repo.get_company_description(conn, "US", "AAA")
        bbb_desc = repo.get_company_description(conn, "US", "BBB")
        ccc_desc = repo.get_company_description(conn, "US", "CCC")
    assert aaa_desc is not None
    assert aaa_desc["description_text"] == "Apple A designs smartphones."
    assert aaa_desc["source"] == "yfinance"
    assert aaa_desc["disclosed_date"] is None
    assert aaa_desc["doc_id"] is None
    assert aaa_desc["fetched_at"]  # テキスト最終変化時刻が入る
    # 欠損・空白のみの銘柄は書かれない（捏造しない・ADR-014）。
    assert bbb_desc is None
    assert ccc_desc is None


def test_stop_mid_loop_breaks(temp_db, monkeypatch) -> None:
    """銘柄境界で should_stop を見て中断し、残り銘柄を焼かない（ADR-036 追補）。

    1 銘柄を焼いた直後に停止要求 → 次銘柄のループ先頭で break。detail に「停止により中断」。
    """
    repo.upsert_us_stocks(
        [
            {"symbol": "AAA", "company_name": "A", "is_etf": 0, "updated_at": "t"},
            {"symbol": "BBB", "company_name": "B", "is_etf": 0, "updated_at": "t"},
            {"symbol": "CCC", "company_name": "C", "is_etf": 0, "updated_at": "t"},
        ]
    )
    monkeypatch.setattr(settings, "us_fundamentals_nightly_max", 5)

    class _StoppingAdapter(_FakeAdapter):
        def fetch_fundamentals(self, symbol):  # type: ignore[override]
            info = super().fetch_fundamentals(symbol)
            state.request_stop()  # 1 銘柄を焼いた直後に停止要求が来た状況を模す
            return info

    fake = _StoppingAdapter({"AAA": _info("A"), "BBB": _info("B"), "CCC": _info("C")})
    state.begin(full_backfill=False)  # request_stop は running 中のみ受理されるため
    try:
        result = fetch_us_fundamentals.run(adapter=fake)  # type: ignore[arg-type]
    finally:
        state.end()

    assert len(fake.calls) == 1  # 2 銘柄目のループ先頭で break
    assert result.ok is True
    assert "停止により中断" in result.detail
