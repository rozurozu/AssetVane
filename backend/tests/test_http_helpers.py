"""_http.py の Throttle / get_with_retry の単体テスト（backend-adapter-pattern）。

担保: Throttle が monotonic 計測で min_interval を空ける（実時間を進めず sleep 引数で検証）・
get_with_retry が 200 を返す／429 をリトライして最終成功／429 枯渇で on_exhausted／429 以外の
4xx で on_http_error 即 raise／retry_network_errors の True/False でネットワーク例外の扱いが
変わる／attempt 毎に throttle.wait() を呼ぶこと。time をモックし実時間に依存しない
（testing-strategy）。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.adapters import _http
from app.adapters._http import Throttle, get_with_retry


class _FakeResponse:
    """status_code/text/content だけ持つ httpx.Response 代用（ダックタイピング）。"""

    def __init__(self, status_code: int, text: str = "", content: bytes = b"") -> None:
        self.status_code = status_code
        self.text = text
        self.content = content


class _FakeClient:
    """get(path, params) を呼ぶたび用意した応答／例外を順に返す fake httpx.Client。"""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        self.calls.append((path, params))
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _err(label: str) -> Callable[..., Exception]:
    """ラベル付き RuntimeError を返す例外ファクトリ（on_http_error/on_exhausted 用）。"""
    return lambda *_: RuntimeError(label)


# --- Throttle -------------------------------------------------------------


def test_throttle_sleeps_when_interval_not_elapsed(monkeypatch) -> None:
    """間隔が空いていなければ不足分だけ sleep する（実時間は進めない）。"""
    times = iter([0.5, 1.6])  # wait() 内: 計算用 0.5 → 更新用 1.6
    sleeps: list[float] = []
    monkeypatch.setattr(_http.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(_http.time, "sleep", lambda s: sleeps.append(s))

    Throttle(1.5).wait()  # 1.5 - (0.5 - 0.0) = 1.0 待つ

    assert sleeps == [1.0]


def test_throttle_no_sleep_when_interval_elapsed(monkeypatch) -> None:
    """前回から十分間隔が空いていれば sleep しない。"""
    times = iter([10.0, 10.0])
    sleeps: list[float] = []
    monkeypatch.setattr(_http.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(_http.time, "sleep", lambda s: sleeps.append(s))

    Throttle(1.0).wait()  # 1.0 - (10.0 - 0.0) < 0 → 待たない

    assert sleeps == []


# --- get_with_retry -------------------------------------------------------


def test_returns_ok_response() -> None:
    """200 はそのまま Response を返す（1 回で成功）。"""
    client = _FakeClient([_FakeResponse(200, text="ok")])
    resp = get_with_retry(client, "/x", on_http_error=_err("http"), on_exhausted=_err("done"))
    assert resp.status_code == 200
    assert len(client.calls) == 1


def test_retries_429_then_succeeds(monkeypatch) -> None:
    """429 を挟んでも最終 200 で成功する（バックオフは実時間消費させない）。"""
    monkeypatch.setattr(_http.time, "sleep", lambda s: None)
    client = _FakeClient([_FakeResponse(429), _FakeResponse(429), _FakeResponse(200)])
    resp = get_with_retry(client, "/x", on_http_error=_err("http"), on_exhausted=_err("done"))
    assert resp.status_code == 200
    assert len(client.calls) == 3


def test_exhausts_on_persistent_429(monkeypatch) -> None:
    """429 が max_retries 回続くと on_exhausted の例外を raise する。"""
    monkeypatch.setattr(_http.time, "sleep", lambda s: None)
    client = _FakeClient([_FakeResponse(429)] * _http.DEFAULT_MAX_RETRIES)
    with pytest.raises(RuntimeError, match="done"):
        get_with_retry(client, "/x", on_http_error=_err("http"), on_exhausted=_err("done"))
    assert len(client.calls) == _http.DEFAULT_MAX_RETRIES


def test_raises_on_http_error_immediately() -> None:
    """429 以外の 4xx は on_http_error で即 raise（リトライしない）。"""
    client = _FakeClient([_FakeResponse(404, text="nope")])
    with pytest.raises(RuntimeError, match="http"):
        get_with_retry(client, "/x", on_http_error=_err("http"), on_exhausted=_err("done"))
    assert len(client.calls) == 1


def test_retries_network_error_when_enabled(monkeypatch) -> None:
    """retry_network_errors=True（既定）は Timeout/Transport 例外もリトライする。"""
    monkeypatch.setattr(_http.time, "sleep", lambda s: None)
    client = _FakeClient([httpx.ConnectError("boom"), _FakeResponse(200)])
    resp = get_with_retry(client, "/x", on_http_error=_err("http"), on_exhausted=_err("done"))
    assert resp.status_code == 200
    assert len(client.calls) == 2


def test_propagates_network_error_when_disabled() -> None:
    """retry_network_errors=False はネットワーク例外を捕まえず呼び出し側へ透過する。"""
    client = _FakeClient([httpx.ConnectError("boom")])
    with pytest.raises(httpx.ConnectError):
        get_with_retry(
            client,
            "/x",
            on_http_error=_err("http"),
            on_exhausted=_err("done"),
            retry_network_errors=False,
        )
    assert len(client.calls) == 1


def test_calls_throttle_each_attempt(monkeypatch) -> None:
    """各 attempt の直前に throttle.wait() を呼ぶ（スロットル→GET の順を保つ）。"""
    monkeypatch.setattr(_http.time, "sleep", lambda s: None)
    waits: list[int] = []

    class _SpyThrottle(Throttle):
        def wait(self) -> None:
            waits.append(1)

    client = _FakeClient([_FakeResponse(429), _FakeResponse(200)])
    get_with_retry(
        client,
        "/x",
        throttle=_SpyThrottle(0.0),
        on_http_error=_err("http"),
        on_exhausted=_err("done"),
    )
    assert len(waits) == 2  # 2 attempt それぞれで wait
