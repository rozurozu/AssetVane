"""JQuantsAdapter の正規化（実 V2 略記キー → 内部列）を固定する。

ネットには出ない。2026-06 に実機確認した実レスポンス形をサンプルにして、
将来キー対応が壊れたら気づけるようにする（docs/data-model.md の対応表と一致）。
"""

from __future__ import annotations

from app.adapters.jquants import (
    JQuantsAdapter,
    _extract_rows,
    _norm_date,
    _to_jq_code,
)

# 実機確認した V2 /v2/equities/bars/daily の 1 行（略記キー）。
BARS_ROW = {
    "Date": "2026-03-09",
    "Code": "72030",
    "O": 3299.0,
    "H": 3423.0,
    "L": 3295.0,
    "C": 3393.0,
    "UL": "0",
    "LL": "0",
    "Vo": 28223800.0,
    "Va": 94872034800.0,
    "AdjFactor": 1.0,
    "AdjC": 3393.0,
}

# 実機確認した V2 /v2/equities/master の 1 行。
MASTER_ROW = {
    "Date": "2026-03-10",
    "Code": "72030",
    "CoName": "トヨタ自動車",
    "S17": "6",
    "S33": "3700",
    "Mkt": "0111",
}


def test_to_jq_code() -> None:
    assert _to_jq_code("7203") == "72030"  # 4 桁 → 5 桁
    assert _to_jq_code("72030") == "72030"  # 既に 5 桁ならそのまま


def test_norm_date() -> None:
    assert _norm_date("20230324") == "2023-03-24"
    assert _norm_date("2023-03-24") == "2023-03-24"


def test_extract_rows() -> None:
    rows, key = _extract_rows({"data": [{"a": 1}], "pagination_key": "k1"})
    assert rows == [{"a": 1}] and key == "k1"
    # フォールバック: "data" 以外の list 値も拾う。
    rows2, key2 = _extract_rows({"other": [{"b": 2}]})
    assert rows2 == [{"b": 2}] and key2 is None


def test_normalize_quote() -> None:
    q = JQuantsAdapter._normalize_quote(BARS_ROW)
    assert q == {
        "code": "72030",
        "date": "2026-03-09",
        "open": 3299.0,
        "high": 3423.0,
        "low": 3295.0,
        "close": 3393.0,
        "volume": 28223800.0,
        "adj_close": 3393.0,
    }


def test_normalize_stock() -> None:
    s = JQuantsAdapter._normalize_stock(MASTER_ROW, "2026-06-02T00:00:00+00:00")
    assert s["code"] == "72030"
    assert s["company_name"] == "トヨタ自動車"
    assert s["sector33_code"] == "3700"
    assert s["sector17_code"] == "6"
    assert s["market_code"] == "0111"
    assert s["is_etf"] == 0
