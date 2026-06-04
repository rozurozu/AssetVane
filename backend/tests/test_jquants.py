"""JQuantsAdapter の正規化（実 V2 略記キー → 内部列）を固定する。

ネットには出ない。2026-06 に実 API 確認した実レスポンス形をサンプルにして、
将来キー対応が壊れたら気づけるようにする（docs/data-model.md の対応表と一致）。
"""

from __future__ import annotations

import httpx
import pytest

from app.adapters import jquants as jq
from app.adapters.jquants import (
    JQuantsAdapter,
    JQuantsCoverageError,
    JQuantsError,
    _extract_rows,
    _norm_date,
    _to_jq_code,
)


class _FakeResp:
    """httpx.Response の最小スタブ（status_code / text / json のみ）。"""

    def __init__(
        self, status_code: int, payload: dict[str, object] | None = None, text: str = ""
    ) -> None:
        self.status_code = status_code
        self.text = text
        self._payload = payload or {"data": []}

    def json(self) -> dict[str, object]:
        return self._payload


class _FakeClient:
    """渡した列を順に返すフェイク httpx.Client。要素が Exception なら get がそれを raise する。"""

    def __init__(self, responses: list[_FakeResp | Exception]) -> None:
        self._responses = list(responses)

    def get(self, path: str, params: dict[str, object]) -> _FakeResp:
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


# 実 API 確認した V2 /v2/equities/bars/daily の 1 行（略記キー）。
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

# 実 API 確認した V2 /v2/equities/master の 1 行。
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


def test_fetch_daily_quotes_by_date() -> None:
    """date だけ指定の全銘柄取得が、正しいパス/パラメータで叩き正規化して返すか。

    実 API 確認済みの挙動（code 無し → その日の全銘柄）をコード側で固定する。ネットは叩かず
    `_get_paginated` を差し替えて、呼び出し引数と正規化結果だけを検証する。
    """
    adapter = JQuantsAdapter(api_key="dummy")  # settings 非依存・ネットも張らない
    calls: list[tuple[str, dict[str, object]]] = []

    def fake_get_paginated(path: str, params: dict[str, object]) -> list[dict[str, object]]:
        calls.append((path, params))
        return [BARS_ROW, {**BARS_ROW, "Code": "67580"}]  # 2 銘柄ぶん

    adapter._get_paginated = fake_get_paginated  # type: ignore[method-assign]
    rows = adapter.fetch_daily_quotes_by_date("2025-12-15")

    assert calls == [("/v2/equities/bars/daily", {"date": "2025-12-15"})]
    assert [r["code"] for r in rows] == ["72030", "67580"]
    assert rows[0]["close"] == 3393.0  # 正規化（C → close）が効いている


def test_normalize_stock() -> None:
    s = JQuantsAdapter._normalize_stock(MASTER_ROW, "2026-06-02T00:00:00+00:00")
    assert s["code"] == "72030"
    assert s["company_name"] == "トヨタ自動車"
    assert s["sector33_code"] == "3700"
    assert s["sector17_code"] == "6"
    assert s["market_code"] == "0111"
    assert s["is_etf"] == 0


# V2 財務エンドポイントの実フィールド名は未確定（jquants.md §6 要再確認）。
# 以下は _first フォールバックの動作を確認するためのサンプル dict を使用する。
FINANCIALS_ROW_FULL = {
    "Code": "72030",
    "DisclosedDate": "2026-05-10",
    "TypeOfCurrentPeriod": "FY2025",
    "NetSales": 45000000000.0,
    "OperatingProfit": 3500000000.0,
    "Profit": 2800000000.0,
    "EarningsPerShare": 850.5,
    "BookValuePerShare": 7200.0,
}

# 略記形式（実 API 確認後に修正・フォールバック機構の動作確認用）。
FINANCIALS_ROW_SHORT = {
    "code": "67580",
    "disclosed_date": "2025-11-15",
    "fiscal_period": "2Q2025",
    "net_sales": 22000000000.0,
    "operating_profit": 1800000000.0,
    "profit": 1400000000.0,
    "eps": 420.0,
    "bps": 6800.0,
}


def test_normalize_financial_full_keys() -> None:
    """V2 フルネームキーが内部列名に正規化される（_first フォールバック確認）。"""
    row = JQuantsAdapter._normalize_financial(FINANCIALS_ROW_FULL)
    assert row["code"] == "72030"
    assert row["disclosed_date"] == "2026-05-10"
    assert row["fiscal_period"] == "FY2025"
    assert row["net_sales"] == 45000000000.0
    assert row["operating_profit"] == 3500000000.0
    assert row["profit"] == 2800000000.0
    assert row["eps"] == 850.5
    assert row["bps"] == 7200.0


def test_normalize_financial_short_keys() -> None:
    """内部列名キー（略記形式）も _first フォールバックで正規化される。"""
    row = JQuantsAdapter._normalize_financial(FINANCIALS_ROW_SHORT)
    assert row["code"] == "67580"
    assert row["disclosed_date"] == "2025-11-15"
    assert row["fiscal_period"] == "2Q2025"
    assert row["net_sales"] == 22000000000.0
    assert row["eps"] == 420.0
    assert row["bps"] == 6800.0


def test_get_with_retry_survives_429_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """429 が連続しても上限まで待ってリトライし、回復したら成功する（5分ブロック耐性）。

    本番投入の実走（2026-06-04）で 429×4 が fetch_quotes を殺した回帰防止。_throttle は
    no-op 化し、429 バックオフの待機列だけを観測する（base × 2^attempt の指数）。
    """
    adapter = JQuantsAdapter(api_key="dummy")
    monkeypatch.setattr(adapter, "_throttle", lambda: None)
    sleeps: list[float] = []
    monkeypatch.setattr(jq.time, "sleep", lambda s: sleeps.append(s))

    client = _FakeClient([_FakeResp(429), _FakeResp(429), _FakeResp(200, {"data": [{"ok": 1}]})])
    payload = adapter._get_with_retry(client, "/v2/equities/bars/daily", {})  # type: ignore[arg-type]

    assert payload == {"data": [{"ok": 1}]}
    assert sleeps == [2.0, 4.0]  # 429 が 2 回 → 指数バックオフ 2,4 秒


def test_get_with_retry_exhausts_and_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """429 が上限回数続いたら待機列が上限 120 秒で頭打ちし、最終的に JQuantsError を投げる。"""
    adapter = JQuantsAdapter(api_key="dummy")
    monkeypatch.setattr(adapter, "_throttle", lambda: None)
    sleeps: list[float] = []
    monkeypatch.setattr(jq.time, "sleep", lambda s: sleeps.append(s))

    client = _FakeClient([_FakeResp(429) for _ in range(jq._MAX_RETRIES)])
    with pytest.raises(JQuantsError, match="429"):
        adapter._get_with_retry(client, "/v2/equities/bars/daily", {})  # type: ignore[arg-type]

    # 合計待機が約6分（2+4+8+16+32+64+120+120）になるまで耐えてから諦める。
    assert sleeps == [2.0, 4.0, 8.0, 16.0, 32.0, 64.0, 120.0, 120.0]


def test_get_with_retry_survives_transient_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """一時的な通信失敗（ReadTimeout/ConnectError）も握って再試行し、回復したら成功する。

    本番投入の実走（2026-06-04）で 429 を耐えた後に単発 ReadTimeout が fetch_quotes を殺した
    回帰防止。429 と同じ指数バックオフで再試行する。
    """
    adapter = JQuantsAdapter(api_key="dummy")
    monkeypatch.setattr(adapter, "_throttle", lambda: None)
    sleeps: list[float] = []
    monkeypatch.setattr(jq.time, "sleep", lambda s: sleeps.append(s))

    client = _FakeClient(
        [
            httpx.ReadTimeout("read timed out"),
            httpx.ConnectError("connection failed"),
            _FakeResp(200, {"data": [{"ok": 1}]}),
        ]
    )
    payload = adapter._get_with_retry(client, "/v2/equities/bars/daily", {})  # type: ignore[arg-type]

    assert payload == {"data": [{"ok": 1}]}
    assert sleeps == [2.0, 4.0]  # ReadTimeout・ConnectError の 2 回ぶん再試行


def test_get_with_retry_raises_after_persistent_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """通信失敗が上限回数続いたら、最後の例外名を添えて JQuantsError を投げる。"""
    adapter = JQuantsAdapter(api_key="dummy")
    monkeypatch.setattr(adapter, "_throttle", lambda: None)
    monkeypatch.setattr(jq.time, "sleep", lambda s: None)

    client = _FakeClient([httpx.ReadTimeout("read timed out") for _ in range(jq._MAX_RETRIES)])
    with pytest.raises(JQuantsError, match="ReadTimeout"):
        adapter._get_with_retry(client, "/v2/equities/bars/daily", {})  # type: ignore[arg-type]


def test_get_with_retry_coverage_400_raises_coverage_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """範囲外日付の 400（'covers the following dates'）は JQuantsCoverageError で送出する。

    本番投入の実走（2026-06-04）で判明。前線到達＝正常終了の合図なので、ふつうの 400 と区別する。
    """
    adapter = JQuantsAdapter(api_key="dummy")
    monkeypatch.setattr(adapter, "_throttle", lambda: None)

    msg = '{"message": "Your subscription covers the following dates: 2024-03-12 ~ 2026-03-12"}'
    client = _FakeClient([_FakeResp(400, text=msg)])
    with pytest.raises(JQuantsCoverageError, match="契約範囲外"):
        adapter._get_with_retry(client, "/v2/equities/bars/daily", {})  # type: ignore[arg-type]


def test_get_with_retry_other_400_raises_plain_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """範囲外メッセージを含まない 400 は通常の JQuantsError（CoverageError ではない）。"""
    adapter = JQuantsAdapter(api_key="dummy")
    monkeypatch.setattr(adapter, "_throttle", lambda: None)

    client = _FakeClient([_FakeResp(400, text='{"message": "bad request"}')])
    with pytest.raises(JQuantsError) as exc_info:
        adapter._get_with_retry(client, "/v2/equities/bars/daily", {})  # type: ignore[arg-type]
    assert not isinstance(exc_info.value, JQuantsCoverageError)
