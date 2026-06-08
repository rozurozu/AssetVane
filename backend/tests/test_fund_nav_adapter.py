"""投信 NAV アダプタ（adapters/fund_nav.py）の正規化テスト（ADR-054・ADR-010/018）。

ネットには出さず、Shift_JIS(cp932) のサンプル CSV をバイト列でスタブ httpx.Client から返し、
外部列名（年月日/基準価額(円)）→ 内部列名（date/nav）の正規化と、HTML/空応答の
FundNavFetchError、from_/to のクライアント側絞り込みを検証する（testing-strategy）。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from app.adapters.fund_nav import FundNavAdapter, FundNavFetchError

ISIN = "JP90C000ABC1"
ASSOC = "0331234A"

# 実機 CSV を模した Shift_JIS サンプル（ヘッダは年月日,基準価額(円),...）。
_SAMPLE_CSV_TEXT = (
    "年月日,基準価額(円),純資産総額（百万円）,分配金,決算期\r\n"
    "2026年06月02日,38000,12345,0,1\r\n"
    "2026年06月03日,38500,12400,0,1\r\n"
    "2026年06月04日,37800,12200,0,1\r\n"
)


def _make_client_stub(content: bytes, status_code: int = 200) -> httpx.Client:
    """httpx.Client のスタブ。get() が指定バイト列 content の Response を返す（ネットに出ない）。"""
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = status_code
    mock_resp.content = content
    mock_resp.text = content.decode("cp932", errors="replace")

    mock_client = MagicMock(spec=httpx.Client)
    mock_client.get.return_value = mock_resp
    return mock_client


def test_normalizes_sjis_csv_to_internal_rows() -> None:
    """Shift_JIS CSV の 年月日/基準価額(円) が date('YYYY-MM-DD')/nav(float) に正規化される。"""
    stub = _make_client_stub(_SAMPLE_CSV_TEXT.encode("cp932"))
    adapter = FundNavAdapter(client=stub)

    rows = adapter.fetch_nav_history(ISIN, assoc_code=ASSOC)

    assert rows == [
        {"isin": ISIN, "date": "2026-06-02", "nav": 38000.0},
        {"isin": ISIN, "date": "2026-06-03", "nav": 38500.0},
        {"isin": ISIN, "date": "2026-06-04", "nav": 37800.0},
    ]
    # nav は float 化されている
    assert all(isinstance(r["nav"], float) for r in rows)


def test_from_to_client_side_filter() -> None:
    """from_/to はクライアント側で絞られる（CSV は全履歴を返すため・ADR-002）。"""
    stub = _make_client_stub(_SAMPLE_CSV_TEXT.encode("cp932"))
    adapter = FundNavAdapter(client=stub)

    rows = adapter.fetch_nav_history(ISIN, assoc_code=ASSOC, from_="2026-06-03", to="2026-06-03")

    assert len(rows) == 1
    assert rows[0]["date"] == "2026-06-03"
    assert rows[0]["nav"] == pytest.approx(38500.0)


def test_missing_assoc_code_raises() -> None:
    """assoc_code 未指定は FundNavFetchError（黙って 0 行にしない・ADR-018）。"""
    stub = _make_client_stub(_SAMPLE_CSV_TEXT.encode("cp932"))
    adapter = FundNavAdapter(client=stub)

    with pytest.raises(FundNavFetchError):
        adapter.fetch_nav_history(ISIN, assoc_code=None)


def test_html_injection_raises() -> None:
    """HTML/JSON 混入（bot よけ・パラメータ不足）は FundNavFetchError（ADR-018）。"""
    html = b'{"statusCode":null}'
    stub = _make_client_stub(html)
    adapter = FundNavAdapter(client=stub)

    with pytest.raises(FundNavFetchError):
        adapter.fetch_nav_history(ISIN, assoc_code=ASSOC)


def test_empty_response_raises() -> None:
    """空応答も想定 CSV ヘッダで始まらないため FundNavFetchError（ADR-018）。"""
    stub = _make_client_stub(b"")
    adapter = FundNavAdapter(client=stub)

    with pytest.raises(FundNavFetchError):
        adapter.fetch_nav_history(ISIN, assoc_code=ASSOC)


def test_http_error_raises() -> None:
    """HTTP 4xx/5xx は FundNavFetchError（ADR-018）。"""
    stub = _make_client_stub(b"server error", status_code=500)
    adapter = FundNavAdapter(client=stub)

    with pytest.raises(FundNavFetchError):
        adapter.fetch_nav_history(ISIN, assoc_code=ASSOC)
