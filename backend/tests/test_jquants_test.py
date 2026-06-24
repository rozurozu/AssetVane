"""J-Quants 疎通テストの脳と口を固定する（ADR-008/011/036/061）。

担保: キー未設定（jquants_config 未登録）は configured=False で fetch_master を呼ばない、認証成功で
ok=True＋会社名、JQuantsError は握って ok=False。エンドポイントは未設定でも 200 で結果フラグを返す。
接続値は DB（jquants_config）から解決し、実 API は叩かず build_jquants_adapter をモックする
（ADR-061・[[testing-strategy]]）。
"""

from __future__ import annotations

import pytest

from app.adapters.jquants import JQuantsError
from app.db import repo
from app.db.engine import get_engine
from app.services import diagnostics


def _set_jquants_key(api_key: str, plan: str = "free") -> None:
    """temp DB の jquants_config に 1 行入れる（ADR-061・W2 を begin で束ねる）。"""
    with get_engine().begin() as conn:
        repo.upsert_jquants_config(conn, {"api_key": api_key, "plan": plan})


def test_not_configured_when_key_unset(temp_db, monkeypatch: pytest.MonkeyPatch) -> None:
    """キー未設定（行なし）なら configured=False・ok=False で、アダプタは生成すらしない。"""
    called = {"n": 0}
    monkeypatch.setattr(
        "app.services.diagnostics.build_jquants_adapter",
        lambda *a, **k: called.__setitem__("n", called["n"] + 1),
    )

    with get_engine().connect() as conn:
        result = diagnostics.check_jquants(conn)
    assert result.configured is False
    assert result.ok is False
    assert called["n"] == 0


def test_ok_on_success(temp_db, monkeypatch: pytest.MonkeyPatch) -> None:
    """認証成功（1 銘柄返る）で ok=True、detail に会社名が載る。"""

    class _Adapter:
        def fetch_master(self, codes: list[str]) -> list[dict]:
            return [{"code": "72030", "company_name": "トヨタ自動車"}]

    _set_jquants_key("dummy")
    monkeypatch.setattr(
        "app.services.diagnostics.build_jquants_adapter", lambda *a, **k: _Adapter()
    )

    with get_engine().connect() as conn:
        result = diagnostics.check_jquants(conn)
    assert result.configured is True
    assert result.ok is True
    assert "トヨタ自動車" in result.detail


def test_not_ok_on_jquants_error(temp_db, monkeypatch: pytest.MonkeyPatch) -> None:
    """JQuantsError（キー不正等）は握って configured=True・ok=False で返す。"""

    class _Adapter:
        def fetch_master(self, codes: list[str]) -> list[dict]:
            raise JQuantsError("401 Unauthorized")

    _set_jquants_key("bad")
    monkeypatch.setattr(
        "app.services.diagnostics.build_jquants_adapter", lambda *a, **k: _Adapter()
    )

    with get_engine().connect() as conn:
        result = diagnostics.check_jquants(conn)
    assert result.configured is True
    assert result.ok is False
    assert "401" in result.detail


def test_endpoint_returns_result_flags(client) -> None:
    """POST /diagnostics/jquants-test は未設定でも 200 で {configured,ok,detail} を返す。"""
    resp = client.post("/diagnostics/jquants-test")
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is False
    assert body["ok"] is False
    assert "detail" in body
