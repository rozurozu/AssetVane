"""IndexAdapter の単体テスト（ネット非依存・phase2-spec.md §8）。

Stooq CSV 文字列を返すスタブ（httpx.Client モック）で `fetch_index_quotes` が
{symbol, date, close} に正規化することを検証する。実 API は叩かない。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from app.adapters.index import IndexAdapter, IndexAdapterError

# テスト用 CSV（Stooq の実形式）
_SAMPLE_CSV = """\
Date,Open,High,Low,Close,Volume
2026-05-28,5304.07,5308.20,5200.11,5250.36,1234567
2026-05-29,5251.00,5280.00,5240.00,5265.00,987654
2026-05-30,5266.00,5290.00,5255.00,5278.90,1111111
"""

# 日付が YYYYMMDD 形式の CSV（_norm_date の正規化を確認）
_SAMPLE_CSV_COMPACT_DATE = """\
Date,Open,High,Low,Close,Volume
20260528,5304.07,5308.20,5200.11,5250.36,1234567
"""


def _make_client_stub(text: str, status_code: int = 200) -> httpx.Client:
    """httpx.Client のスタブを作る。get() が指定 text の Response を返す。"""
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = status_code
    mock_resp.text = text

    mock_client = MagicMock(spec=httpx.Client)
    mock_client.get.return_value = mock_resp
    return mock_client


def test_fetch_index_quotes_normalizes_fields() -> None:
    """CSV の Date/Close が symbol・date・close に正規化される。"""
    stub = _make_client_stub(_SAMPLE_CSV)
    adapter = IndexAdapter(client=stub)

    rows = adapter.fetch_index_quotes("^SPX")

    assert len(rows) == 3
    # すべての行に symbol が付く
    assert all(r["symbol"] == "^SPX" for r in rows)
    # date は 'YYYY-MM-DD' 形式
    assert rows[0]["date"] == "2026-05-28"
    assert rows[1]["date"] == "2026-05-29"
    assert rows[2]["date"] == "2026-05-30"
    # close は float に変換されている
    assert rows[0]["close"] == pytest.approx(5250.36)
    assert rows[2]["close"] == pytest.approx(5278.90)
    # Open/High/Low/Volume 列は除外されている
    assert "open" not in rows[0]
    assert "high" not in rows[0]
    assert "volume" not in rows[0]


def test_fetch_index_quotes_compact_date() -> None:
    """YYYYMMDD 形式の日付が 'YYYY-MM-DD' に正規化される。"""
    stub = _make_client_stub(_SAMPLE_CSV_COMPACT_DATE)
    adapter = IndexAdapter(client=stub)

    rows = adapter.fetch_index_quotes("^NKX")

    assert len(rows) == 1
    assert rows[0]["date"] == "2026-05-28"
    assert rows[0]["symbol"] == "^NKX"


def test_fetch_index_quotes_empty_csv() -> None:
    """空 CSV（ヘッダのみ）は空リストを返す。"""
    stub = _make_client_stub("Date,Open,High,Low,Close,Volume\n")
    adapter = IndexAdapter(client=stub)

    rows = adapter.fetch_index_quotes("^TPX")

    assert rows == []


def test_fetch_index_quotes_no_data_row() -> None:
    """'No data' 行はスキップされる。"""
    stub = _make_client_stub("Date,Open,High,Low,Close,Volume\nNo data\n")
    adapter = IndexAdapter(client=stub)

    rows = adapter.fetch_index_quotes("^SPX")

    assert rows == []


def test_fetch_index_quotes_http_error_raises() -> None:
    """HTTP 4xx はそのまま IndexAdapterError に変換される。"""
    stub = _make_client_stub("Not Found", status_code=404)
    adapter = IndexAdapter(client=stub)

    with pytest.raises(IndexAdapterError, match="404"):
        adapter.fetch_index_quotes("^SPX")


def test_fetch_index_quotes_invalid_close_skipped() -> None:
    """Close が数値に変換できない行はスキップされる。"""
    csv_text = "Date,Open,High,Low,Close,Volume\n2026-05-28,5000,5100,4900,N/A,1000\n"
    stub = _make_client_stub(csv_text)
    adapter = IndexAdapter(client=stub)

    rows = adapter.fetch_index_quotes("^SPX")

    assert rows == []


def test_fetch_index_quotes_passes_from_to_params() -> None:
    """from_・to が日付パラメータ（d1・d2）としてリクエストに渡される。"""
    stub = _make_client_stub(_SAMPLE_CSV)
    adapter = IndexAdapter(client=stub)

    adapter.fetch_index_quotes("^SPX", from_="2026-05-01", to="2026-05-31")

    call_kwargs = stub.get.call_args
    params = call_kwargs[1].get("params", call_kwargs[0][1] if len(call_kwargs[0]) > 1 else {})
    # from_ が YYYYMMDD 形式で渡されていることを確認
    assert params.get("d1") == "20260501"
    assert params.get("d2") == "20260531"
