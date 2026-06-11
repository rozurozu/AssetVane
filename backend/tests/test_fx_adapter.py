"""FxAdapter の単体テスト（ADR-010/057・adapters/fx.py）。

fetch_fx_rates ジョブのテストは FakeSource を sources= で注入するため、YahooFxSource の
DataFrame 正規化（_rows_from_df）を素通りする。ここでは fake な yfinance fetch を注入して
**正規化そのもの**（MultiIndex 平坦化・NaN 除去・0 行エラー・JPY=X 解決）とファサードの
フォールバック連鎖・未知ペアを固定する。ネット（実 yfinance）には出ない（testing-strategy）。
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.adapters.fx import FxAdapter, FxAdapterError, FxSource, YahooFxSource


def _df(dates: list[str], closes: list[float], *, multiindex: bool = False) -> pd.DataFrame:
    """Close 列を持つ日足 DataFrame を組む（multiindex=True で yfinance の 2 階層列を模す）。"""
    idx = pd.to_datetime(dates)
    if multiindex:
        cols = pd.MultiIndex.from_tuples([("Close", "JPY=X")])
        return pd.DataFrame({("Close", "JPY=X"): closes}, index=idx).set_axis(cols, axis=1)
    return pd.DataFrame({"Close": closes}, index=idx)


def test_yahoo_source_normalizes_to_rows() -> None:
    """JPY=X の Close 終値が [{date, pair, rate}] に正規化される（date は YYYY-MM-DD）。"""
    captured: dict[str, object] = {}

    def fake_fetch(ticker: str, start: str | None, end: str | None) -> pd.DataFrame:
        captured["ticker"] = ticker  # USDJPY → JPY=X に解決されているか
        return _df(["2026-06-09", "2026-06-10"], [150.5, 151.25])

    src = YahooFxSource(fetch=fake_fetch)
    rows = src.fetch_rates("USDJPY", from_="2026-06-01", to="2026-06-10")

    assert captured["ticker"] == "JPY=X"
    assert rows == [
        {"date": "2026-06-09", "pair": "USDJPY", "rate": 150.5},
        {"date": "2026-06-10", "pair": "USDJPY", "rate": 151.25},
    ]


def test_yahoo_source_flattens_multiindex_columns() -> None:
    """単一ティッカーが稀に返す MultiIndex 列を平坦化して Close を拾える。"""
    src = YahooFxSource(fetch=lambda t, s, e: _df(["2026-06-10"], [149.9], multiindex=True))
    rows = src.fetch_rates("USDJPY")
    assert rows == [{"date": "2026-06-10", "pair": "USDJPY", "rate": 149.9}]


def test_yahoo_source_drops_nan_close() -> None:
    """Close が NaN の行（休場プレースホルダ）は捨てる（捏造しない・ADR-014）。"""
    src = YahooFxSource(
        fetch=lambda t, s, e: _df(["2026-06-09", "2026-06-10"], [float("nan"), 152.0])
    )
    rows = src.fetch_rates("USDJPY")
    assert rows == [{"date": "2026-06-10", "pair": "USDJPY", "rate": 152.0}]


def test_yahoo_source_empty_raises() -> None:
    """0 行（空 DataFrame）は FxAdapterError を投げ、ファサードが次ソースへ回せる（ADR-018）。"""
    src = YahooFxSource(fetch=lambda t, s, e: pd.DataFrame())
    with pytest.raises(FxAdapterError):
        src.fetch_rates("USDJPY")


def test_yahoo_source_all_nan_raises() -> None:
    """行はあるが有効な close が 0 件なら FxAdapterError（黙って 0 行にしない・ADR-018）。"""
    src = YahooFxSource(fetch=lambda t, s, e: _df(["2026-06-10"], [float("nan")]))
    with pytest.raises(FxAdapterError):
        src.fetch_rates("USDJPY")


def test_unknown_pair_raises() -> None:
    """_PAIR_TO_YAHOO_TICKER に無い通貨ペアは FxAdapterError（直結を 1 か所に閉じる）。"""
    src = YahooFxSource(fetch=lambda t, s, e: _df(["2026-06-10"], [150.0]))
    with pytest.raises(FxAdapterError):
        src.fetch_rates("EURJPY")


class _RaisingSource(FxSource):
    """常に FxAdapterError を投げるソース（フォールバック連鎖の検証用）。"""

    name = "raising"

    def fetch_rates(self, pair, from_=None, to=None):  # noqa: ANN001, ANN201
        raise FxAdapterError("障害（テスト）")


class _OkSource(FxSource):
    """固定行を返すソース（フォールバック先）。"""

    name = "ok"

    def fetch_rates(self, pair, from_=None, to=None):  # noqa: ANN001, ANN201
        return [{"date": "2026-06-10", "pair": pair, "rate": 160.0}]


def test_facade_falls_back_to_next_source() -> None:
    """先頭ソースが失敗したら次ソースへフォールバックする（ADR-010・連鎖）。"""
    adapter = FxAdapter(sources=[_RaisingSource(), _OkSource()])
    rows = adapter.fetch_rates("USDJPY")
    assert rows == [{"date": "2026-06-10", "pair": "USDJPY", "rate": 160.0}]


def test_facade_all_sources_fail_raises() -> None:
    """全ソース失敗で FxAdapterError を上げる（黙って空にしない・ADR-018）。"""
    adapter = FxAdapter(sources=[_RaisingSource(), _RaisingSource()])
    with pytest.raises(FxAdapterError):
        adapter.fetch_rates("USDJPY")
