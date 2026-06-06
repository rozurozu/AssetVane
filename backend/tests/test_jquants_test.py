"""J-Quants 疎通テストの脳と口を固定する（ADR-008/011/036）。

担保: キー未設定は configured=False で fetch_master を呼ばない、認証成功で ok=True＋会社名、
JQuantsError は握って ok=False、DB に触らない。エンドポイントは未設定でも 200 で結果フラグを返す。
実 API は叩かず JQuantsAdapter.fetch_master をモックする（[[testing-strategy]]）。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.adapters.jquants import JQuantsError
from app.config import settings
from app.main import app
from app.services import diagnostics


def test_not_configured_when_key_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """キー未設定なら configured=False・ok=False で、アダプタは生成すらしない。"""
    monkeypatch.setattr(settings, "jquants_api_key", "")
    called = {"n": 0}
    monkeypatch.setattr(
        "app.services.diagnostics.JQuantsAdapter",
        lambda *a, **k: called.__setitem__("n", called["n"] + 1),
    )

    result = diagnostics.check_jquants()
    assert result.configured is False
    assert result.ok is False
    assert called["n"] == 0


def test_ok_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """認証成功（1 銘柄返る）で ok=True、detail に会社名が載る。"""

    class _Adapter:
        def fetch_master(self, codes: list[str]) -> list[dict]:
            return [{"code": "72030", "company_name": "トヨタ自動車"}]

    monkeypatch.setattr(settings, "jquants_api_key", "dummy")
    monkeypatch.setattr("app.services.diagnostics.JQuantsAdapter", lambda *a, **k: _Adapter())

    result = diagnostics.check_jquants()
    assert result.configured is True
    assert result.ok is True
    assert "トヨタ自動車" in result.detail


def test_not_ok_on_jquants_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """JQuantsError（キー不正等）は握って configured=True・ok=False で返す。"""

    class _Adapter:
        def fetch_master(self, codes: list[str]) -> list[dict]:
            raise JQuantsError("401 Unauthorized")

    monkeypatch.setattr(settings, "jquants_api_key", "bad")
    monkeypatch.setattr("app.services.diagnostics.JQuantsAdapter", lambda *a, **k: _Adapter())

    result = diagnostics.check_jquants()
    assert result.configured is True
    assert result.ok is False
    assert "401" in result.detail


def test_endpoint_returns_result_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /diagnostics/jquants-test は未設定でも 200 で {configured,ok,detail} を返す。"""
    monkeypatch.setattr(settings, "jquants_api_key", "")

    with TestClient(app) as client:
        resp = client.post("/diagnostics/jquants-test")
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is False
    assert body["ok"] is False
    assert "detail" in body
