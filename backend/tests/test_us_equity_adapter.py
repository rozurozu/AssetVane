"""UsEquityAdapter / UsEquitySource の単体テスト（ネット非依存・Phase 7(B-1)・ADR-010/039）。

担保: NASDAQ Trader directory パーサの普通株抽出・is_etf 判定・フッタ行除去（fetch_universe）／
`.info` の内部列正規化・欠損→None・operating_profit 近似・YoY 素の受け渡し・business_summary
（`.info.longBusinessSummary` 素のまま・ADR-050 段階A）の受け渡し・空 `.info`（主要キー全欠損）は
UsEquityAdapterError を raise（quotes と契約対称・ADR-018・tasks/review-2026-06-12.md C-4）
（fetch_fundamentals）／OHLCV の内部列正規化（fetch_quotes）／ファサードが UsEquityNotSupported
ソースをスキップして次へ回すこと。実 API（yfinance/NASDAQ）は叩かず、サンプル text/dict と
fake fetch を注入する（testing-strategy）。
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from app.adapters.us_equity import (
    UsEquityAdapter,
    UsEquityAdapterError,
    UsEquityNotSupported,
    UsEquitySource,
    YahooUsEquitySource,
)

# ── サンプル NASDAQ Trader directory（実形式・パイプ区切り・末尾フッタ行付き） ──────────
# nasdaqlisted.txt: 普通株 AAPL／ETF QQQ（is_etf=Y）／優先株（名称で除外）／試験銘柄（Test=Y）。
_NASDAQLISTED = """\
Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares
AAPL|Apple Inc. - Common Stock|Q|N|N|100|N|N
QQQ|Invesco QQQ Trust|Q|N|N|100|Y|N
ABCpA|Some Bank Inc. - 5.00% Preferred Stock|Q|N|N|100|N|N
ZTEST|Nasdaq Test Issue - Common Stock|Q|Y|N|100|N|N
File Creation Time: 0601202612:00|||||||
"""

# otherlisted.txt: 列順が異なる（ACT Symbol／ETF／Test Issue の位置が違う）。NYSE 普通株 BRK と
# ユニット（名称で除外）。
_OTHERLISTED = """\
ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol
BRK|Berkshire Hathaway Inc. Common Stock|N|BRK|N|100|N|BRK
XYZ.U|Some SPAC - Units|N|XYZ.U|N|100|N|XYZ.U
File Creation Time: 0601202612:00|||||||
"""


def _universe_fetch(path: str) -> str:
    """path に応じて nasdaqlisted / otherlisted のサンプル text を返す fake directory_fetch。"""
    if "nasdaqlisted" in path:
        return _NASDAQLISTED
    if "otherlisted" in path:
        return _OTHERLISTED
    raise AssertionError(f"想定外の path: {path}")


def test_fetch_universe_extracts_common_stocks_and_etf_flag() -> None:
    """普通株のみ抽出（優先株/ユニット/試験銘柄を除外）・ETF は is_etf=1 で残す・フッタ行除去。"""
    adapter = UsEquityAdapter(sources=[], directory_fetch=_universe_fetch)
    rows = adapter.fetch_universe()
    by_symbol = {r["symbol"]: r for r in rows}

    # 普通株（NASDAQ AAPL・NYSE BRK）と ETF（QQQ）が残る。
    assert set(by_symbol) == {"AAPL", "QQQ", "BRK"}
    # 優先株・ユニット・試験銘柄は除外され、File Creation Time フッタも入らない。
    assert "ABCpA" not in by_symbol
    assert "XYZ.U" not in by_symbol
    assert "ZTEST" not in by_symbol
    assert not any("File Creation Time" in (r["symbol"] or "") for r in rows)

    # is_etf フラグ: AAPL/BRK=0・QQQ=1。
    assert by_symbol["AAPL"]["is_etf"] == 0
    assert by_symbol["QQQ"]["is_etf"] == 1
    assert by_symbol["BRK"]["is_etf"] == 0
    assert by_symbol["AAPL"]["company_name"] == "Apple Inc. - Common Stock"


def test_fetch_fundamentals_normalizes_info_fields() -> None:
    """`.info` の外部キーが内部列に正規化され・operating_profit が margin×revenue で近似される。"""
    info = {
        "longName": "Apple Inc.",
        "sector": "Technology",
        "industry": "Consumer Electronics",
        "trailingEps": 6.5,
        "bookValue": 4.2,
        "sharesOutstanding": 15_000_000_000,
        "dividendRate": 1.0,
        "totalRevenue": 400_000_000_000,
        "operatingMargins": 0.3,
        "netIncomeToCommon": 100_000_000_000,
        "revenueGrowth": 0.08,
        "earningsGrowth": 0.11,
        "longBusinessSummary": "Apple Inc. designs, manufactures, and markets smartphones.",
    }
    source = YahooUsEquitySource(fetch_info=lambda _s: info)
    snap = source.fetch_fundamentals("AAPL")

    assert snap["company_name"] == "Apple Inc."
    assert snap["gics_sector"] == "Technology"
    assert snap["industry"] == "Consumer Electronics"
    assert snap["eps"] == pytest.approx(6.5)
    assert snap["bps"] == pytest.approx(4.2)
    assert snap["shares_net"] == pytest.approx(15_000_000_000)
    assert snap["dividend_per_share"] == pytest.approx(1.0)
    assert snap["net_sales"] == pytest.approx(400_000_000_000)
    assert snap["profit"] == pytest.approx(100_000_000_000)
    # operating_profit は `.info` 直接になく operatingMargins × totalRevenue の近似（ADR-014）。
    assert snap["operating_profit"] == pytest.approx(0.3 * 400_000_000_000)
    # YoY 素は `.info` 提供の率をそのまま受け渡す（採否は後続ウェーブ）。
    assert snap["revenue_growth_yoy"] == pytest.approx(0.08)
    assert snap["earnings_growth_yoy"] == pytest.approx(0.11)
    # business_summary は `.info.longBusinessSummary` を素のまま渡す（ADR-050 段階A）。
    assert snap["business_summary"] == "Apple Inc. designs, manufactures, and markets smartphones."


def test_fetch_fundamentals_missing_fields_become_none() -> None:
    """欠損フィールドは None に倒れ・近似の素が欠ければ operating_profit も None（捏造しない）。

    主要キーが 1 つでも残っていれば（ここでは gics_sector）正常返却する＝全欠損 raise（C-4）が
    部分欠損のスパースな銘柄を巻き込まないこと。
    """
    source = YahooUsEquitySource(fetch_info=lambda _s: {"sector": "Energy"})
    snap = source.fetch_fundamentals("XOM")

    assert snap["gics_sector"] == "Energy"
    assert snap["eps"] is None
    assert snap["bps"] is None
    assert snap["shares_net"] is None
    assert snap["net_sales"] is None
    assert snap["operating_profit"] is None  # margin/revenue が無いので近似不可
    assert snap["fin_disclosed_date"] is None
    assert snap["revenue_growth_yoy"] is None
    assert snap["business_summary"] is None  # longBusinessSummary 欠損は None（捏造しない）


def test_fetch_fundamentals_empty_info_raises() -> None:
    """空 `.info`（bot 検知/レート制限時の空 dict）は UsEquityAdapterError を raise する（C-4）。

    quotes の「0 行＝raise」と契約対称（ADR-018: 黙って欠損にしない）。全 None の正常返却は
    呼び出し側の partial UPSERT で既存財務値を NULL 上書きしてしまうため許さない。
    """
    source = YahooUsEquitySource(fetch_info=lambda _s: {})
    with pytest.raises(UsEquityAdapterError):
        source.fetch_fundamentals("AAPL")


def test_fetch_fundamentals_all_major_keys_missing_raises() -> None:
    """主要キーに対応しない無関係キーだけの `.info`（実質空）も raise する（C-4）。

    yfinance の bot 検知応答は {"trailingPegRatio": None} 等のゴミだけ返ることがある。
    内部列に正規化すると全 None になるため、空 dict と同様に契約違反として倒す。
    """
    source = YahooUsEquitySource(fetch_info=lambda _s: {"trailingPegRatio": None, "maxAge": 86400})
    with pytest.raises(UsEquityAdapterError):
        source.fetch_fundamentals("AAPL")


def test_fetch_quotes_normalizes_ohlcv() -> None:
    """OHLCV DataFrame が {symbol,date,open,high,low,close,volume,adj_close} に正規化される。"""
    df = pd.DataFrame(
        {
            "Open": [180.0, 182.0],
            "High": [185.0, 186.0],
            "Low": [179.0, 181.0],
            "Close": [184.0, 185.5],
            "Volume": [1_000_000, 1_200_000],
            "Adj Close": [183.5, 185.0],
        },
        index=pd.to_datetime(["2026-05-28", "2026-05-29"]),
    )
    source = YahooUsEquitySource(fetch_quotes=lambda _s, _f, _t: df)
    rows = source.fetch_quotes("AAPL", "2026-05-28", "2026-05-29")

    assert len(rows) == 2
    first = rows[0]
    assert first["symbol"] == "AAPL"
    assert first["date"] == "2026-05-28"
    assert first["open"] == pytest.approx(180.0)
    assert first["close"] == pytest.approx(184.0)
    assert first["volume"] == pytest.approx(1_000_000)
    assert first["adj_close"] == pytest.approx(183.5)


def test_fetch_quotes_empty_raises() -> None:
    """0 行（None/空 DataFrame）は UsEquityAdapterError（黙って 0 行にしない・ADR-018）。"""
    source = YahooUsEquitySource(fetch_quotes=lambda _s, _f, _t: None)
    with pytest.raises(UsEquityAdapterError):
        source.fetch_quotes("AAPL")


class _NotSupportedSource(UsEquitySource):
    """全関心を UsEquityNotSupported で投げるダミーソース（フォールバックのスキップ検証用）。"""

    name = "dummy"

    def fetch_quotes(
        self, symbol: str, from_: str | None = None, to: str | None = None
    ) -> list[dict[str, Any]]:
        raise UsEquityNotSupported("dummy は quotes 未対応")

    def fetch_fundamentals(self, symbol: str) -> dict[str, Any]:
        raise UsEquityNotSupported("dummy は fundamentals 未対応")


class _OkSource(UsEquitySource):
    """常に成功する後段ソース（前段スキップ後に採用されるか検証用）。"""

    name = "ok"

    def fetch_quotes(
        self, symbol: str, from_: str | None = None, to: str | None = None
    ) -> list[dict[str, Any]]:
        return [{"symbol": symbol, "date": "2026-05-29", "close": 1.0}]

    def fetch_fundamentals(self, symbol: str) -> dict[str, Any]:
        return {"company_name": "Ok Co", "eps": 1.0}


def test_facade_skips_not_supported_source() -> None:
    """UsEquityNotSupported を投げる前段ソースを握り、次の成功ソースを採用する。"""
    adapter = UsEquityAdapter(sources=[_NotSupportedSource(), _OkSource()])
    quotes = adapter.fetch_quotes("AAPL")
    assert quotes[0]["symbol"] == "AAPL"
    fundamentals = adapter.fetch_fundamentals("AAPL")
    assert fundamentals["company_name"] == "Ok Co"


def test_facade_all_sources_fail_raises() -> None:
    """全ソールが未対応/失敗なら UsEquityAdapterError を raise する。"""
    adapter = UsEquityAdapter(sources=[_NotSupportedSource()])
    with pytest.raises(UsEquityAdapterError):
        adapter.fetch_quotes("AAPL")


# ── バルク取得（fetch_quotes_bulk・ADR-055 当初設計「yf.download バッチ一括」への収束） ──────
def _bulk_df(symbols: list[str]) -> pd.DataFrame:
    """group_by='ticker' の yf.download 戻り（columns=(ticker,種別) の 2 階層）を模す fake DF。"""
    tuples: list[tuple[str, str]] = []
    data_row1: list[float] = []
    data_row2: list[float] = []
    for i, s in enumerate(symbols):
        base = 100.0 + i * 10
        for kind, v in (
            ("Open", base),
            ("High", base + 5),
            ("Low", base - 5),
            ("Close", base + 1),
            ("Volume", 1_000_000.0 + i),
            ("Adj Close", base + 0.5),
        ):
            tuples.append((s, kind))
            data_row1.append(v)
            data_row2.append(v + 1)
    cols = pd.MultiIndex.from_tuples(tuples)
    return pd.DataFrame(
        [data_row1, data_row2],
        index=pd.to_datetime(["2026-05-28", "2026-05-29"]),
        columns=cols,
    )


def test_fetch_quotes_bulk_splits_multiindex() -> None:
    """MultiIndex を symbol 別に分解し内部列名へ正規化する（_rows_from_df 再利用）。"""
    df = _bulk_df(["AA", "AAPL"])
    source = YahooUsEquitySource(fetch_quotes_bulk=lambda _syms, _f, _t: df)
    result = source.fetch_quotes_bulk(["AA", "AAPL"], "2026-05-28", "2026-05-29")

    assert set(result) == {"AA", "AAPL"}
    assert len(result["AA"]) == 2
    first = result["AAPL"][0]
    assert first["symbol"] == "AAPL"
    assert first["date"] == "2026-05-28"
    assert first["close"] == pytest.approx(111.0)  # base=110, Close=base+1
    assert first["adj_close"] == pytest.approx(110.5)


def test_fetch_quotes_bulk_partial_missing_drops_symbol() -> None:
    """応答に含まれない symbol は dict から落ちる（部分欠損・取れた symbol だけ返す）。"""
    df = _bulk_df(["AA"])  # AAPL は応答に無い
    source = YahooUsEquitySource(fetch_quotes_bulk=lambda _syms, _f, _t: df)
    result = source.fetch_quotes_bulk(["AA", "AAPL"], "2026-05-28", "2026-05-29")
    assert set(result) == {"AA"}  # AAPL は黙って drop（呼び出し側が欠損を数える）


def test_fetch_quotes_bulk_empty_raises() -> None:
    """バッチ全滅（None/空 DataFrame）は UsEquityAdapterError（単数版と契約対称・ADR-018）。"""
    source = YahooUsEquitySource(fetch_quotes_bulk=lambda _syms, _f, _t: None)
    with pytest.raises(UsEquityAdapterError):
        source.fetch_quotes_bulk(["AA", "AAPL"], "2026-05-28", "2026-05-29")


def test_fetch_universe_rejects_numeric_symbol() -> None:
    """純数字 symbol（過去に列ズレで混入した日本株コード）を弾く（ADR-055 再発防止）。"""
    nasdaq = (
        "Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares\n"  # noqa: E501 — NASDAQ Trader の実ヘッダ（分割で可読性低下）
        "AAPL|Apple Inc. - Common Stock|Q|N|N|100|N|N\n"
        "18330|1488184|Q|N|N|100|N|N\n"  # 純数字＝過去の列ズレ混入を模す
        "File Creation Time: 0601202612:00|||||||\n"
    )
    other = (
        "ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol\n"
        "File Creation Time: 0601202612:00|||||||\n"
    )

    def fetch(path: str) -> str:
        return nasdaq if "nasdaqlisted" in path else other

    adapter = UsEquityAdapter(sources=[], directory_fetch=fetch)
    by_symbol = {r["symbol"] for r in adapter.fetch_universe()}
    assert "AAPL" in by_symbol
    assert "18330" not in by_symbol  # 純数字は入口で弾かれる
