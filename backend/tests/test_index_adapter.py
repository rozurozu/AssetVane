"""IndexAdapter / IndexSource の単体テスト（ネット非依存・phase2-spec.md §8）。

前半: StooqIndexSource を Stooq CSV 文字列スタブ（httpx.Client モック）で検証
（`fetch_index_quotes` が {symbol, date, close} に正規化・bot よけ HTML 等は raise）。
後半: IndexAdapter ファサードのフォールバック連鎖（例外で次へ・空は採用・全滅で raise）を
fake source で検証。実 API は叩かない。
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import MagicMock

import httpx
import pytest

from app.adapters.index import (
    IndexAdapter,
    IndexAdapterError,
    IndexSource,
    StooqIndexSource,
)

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
    source = StooqIndexSource(client=stub)

    rows = source.fetch_index_quotes("^SPX")

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
    source = StooqIndexSource(client=stub)

    rows = source.fetch_index_quotes("^NKX")

    assert len(rows) == 1
    assert rows[0]["date"] == "2026-05-28"
    assert rows[0]["symbol"] == "^NKX"


def test_fetch_index_quotes_empty_csv() -> None:
    """空 CSV（ヘッダのみ）は空リストを返す。"""
    stub = _make_client_stub("Date,Open,High,Low,Close,Volume\n")
    source = StooqIndexSource(client=stub)

    rows = source.fetch_index_quotes("^TPX")

    assert rows == []


def test_fetch_index_quotes_no_data_row() -> None:
    """'No data' 行はスキップされる。"""
    stub = _make_client_stub("Date,Open,High,Low,Close,Volume\nNo data\n")
    source = StooqIndexSource(client=stub)

    rows = source.fetch_index_quotes("^SPX")

    assert rows == []


def test_fetch_index_quotes_http_error_raises() -> None:
    """HTTP 4xx はそのまま IndexAdapterError に変換される。"""
    stub = _make_client_stub("Not Found", status_code=404)
    source = StooqIndexSource(client=stub)

    with pytest.raises(IndexAdapterError, match="404"):
        source.fetch_index_quotes("^SPX")


def test_fetch_index_quotes_invalid_close_skipped() -> None:
    """Close が数値に変換できない行はスキップされる。"""
    csv_text = "Date,Open,High,Low,Close,Volume\n2026-05-28,5000,5100,4900,N/A,1000\n"
    stub = _make_client_stub(csv_text)
    source = StooqIndexSource(client=stub)

    rows = source.fetch_index_quotes("^SPX")

    assert rows == []


_HTML_CHALLENGE = (
    '<!DOCTYPE html><html><head><meta charset="utf-8">'
    '<meta name="robots" content="noindex,nofollow"></head><body>'
    "<noscript>This site requires JavaScript to verify your browser. "
    "Please enable JavaScript and reload.</noscript><script>...</script></body></html>"
)


def test_fetch_index_quotes_html_challenge_raises() -> None:
    """CSV でなく bot よけ HTML（200）が返ったら静かに 0 行にせず raise（ADR-018/038）。"""
    stub = _make_client_stub(_HTML_CHALLENGE)
    source = StooqIndexSource(client=stub)

    with pytest.raises(IndexAdapterError, match="CSV を返しませんでした"):
        source.fetch_index_quotes("^SPX")


def test_fetch_index_quotes_empty_body_raises() -> None:
    """空応答（200・本文ゼロ）も CSV ではないので raise する。"""
    stub = _make_client_stub("")
    source = StooqIndexSource(client=stub)

    with pytest.raises(IndexAdapterError, match="CSV を返しませんでした"):
        source.fetch_index_quotes("^NKX")


def test_fetch_index_quotes_rate_limit_message_raises() -> None:
    """Stooq のレート制限メッセージ（CSV でない平文・200）も raise する。"""
    stub = _make_client_stub("Exceeded the daily hits limit!\n")
    source = StooqIndexSource(client=stub)

    with pytest.raises(IndexAdapterError, match="CSV を返しませんでした"):
        source.fetch_index_quotes("^TPX")


def test_fetch_index_quotes_passes_from_to_params() -> None:
    """from_・to が日付パラメータ（d1・d2）としてリクエストに渡される。"""
    stub = _make_client_stub(_SAMPLE_CSV)
    source = StooqIndexSource(client=stub)

    source.fetch_index_quotes("^SPX", from_="2026-05-01", to="2026-05-31")

    call_kwargs = cast(Any, stub.get).call_args
    params = call_kwargs[1].get("params", call_kwargs[0][1] if len(call_kwargs[0]) > 1 else {})
    # from_ が YYYYMMDD 形式で渡されていることを確認
    assert params.get("d1") == "20260501"
    assert params.get("d2") == "20260531"


# ---------------------------------------------------------------------------
# IndexAdapter（ファサード）のフォールバック連鎖（grill 2026-06・ネット非依存）
# ---------------------------------------------------------------------------
class _FakeSource(IndexSource):
    """テスト用 fake source。例外を投げるか固定行を返すかを注入する。"""

    def __init__(
        self, name: str, *, rows: list[dict[str, Any]] | None = None, exc: Exception | None = None
    ) -> None:
        self.name = name
        self._rows = rows
        self._exc = exc
        self.calls = 0

    def fetch_index_quotes(
        self, symbol: str, from_: str | None = None, to: str | None = None
    ) -> list[dict[str, Any]]:
        self.calls += 1
        if self._exc is not None:
            raise self._exc
        return list(self._rows or [])


def test_facade_falls_back_on_exception() -> None:
    """先頭ソースが例外なら次ソースの結果を採用する。"""
    s1 = _FakeSource("s1", exc=IndexAdapterError("bot よけ"))
    s2 = _FakeSource("s2", rows=[{"symbol": "^SPX", "date": "2026-05-28", "close": 5250.0}])
    adapter = IndexAdapter(sources=[s1, s2])

    rows = adapter.fetch_index_quotes("^SPX")

    assert rows == [{"symbol": "^SPX", "date": "2026-05-28", "close": 5250.0}]
    assert s1.calls == 1 and s2.calls == 1


def test_facade_empty_success_stops_chain() -> None:
    """先頭ソースが成功（0 行＝正規の空）なら採用して打ち切り、次は呼ばない。"""
    s1 = _FakeSource("s1", rows=[])
    s2 = _FakeSource("s2", rows=[{"symbol": "^SPX", "date": "2026-05-28", "close": 1.0}])
    adapter = IndexAdapter(sources=[s1, s2])

    rows = adapter.fetch_index_quotes("^SPX")

    assert rows == []
    assert s1.calls == 1 and s2.calls == 0  # s2 は呼ばれない


def test_facade_all_sources_fail_raises() -> None:
    """全ソースが例外なら IndexAdapterError（理由を集約）。"""
    s1 = _FakeSource("s1", exc=IndexAdapterError("x"))
    s2 = _FakeSource("s2", exc=IndexAdapterError("y"))
    adapter = IndexAdapter(sources=[s1, s2])

    with pytest.raises(IndexAdapterError, match="全ソースで"):
        adapter.fetch_index_quotes("^SPX")


def test_facade_builds_from_config_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """index_sources（CSV・優先順）を _REGISTRY で解決し、未知名はスキップする。"""
    from app.config import settings

    monkeypatch.setattr(settings, "index_sources", "bogus,stooq")
    adapter = IndexAdapter()  # 設定から構築

    # 未知 'bogus' はスキップされ、stooq だけが構築される
    assert [s.name for s in adapter._sources] == ["stooq"]
    assert isinstance(adapter._sources[0], StooqIndexSource)
