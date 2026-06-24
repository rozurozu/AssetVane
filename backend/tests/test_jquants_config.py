"""J-Quants 接続設定（jquants_config）の repo・resolver・REST を固定する（ADR-061）。

担保: 単一行 upsert の冪等・write-only（api_key 空送信は据え置き）・GET でのマスク・plan 中継・
未登録時の resolve None / current_plan="free"。ネットに出ず一時 SQLite で回す（testing-strategy）。
"""

from __future__ import annotations

from typing import Any

from app.db import repo
from app.db.engine import get_engine
from app.services.jquants_config import current_plan, resolve_jquants_config


def _set(fields: dict[str, Any]) -> None:
    with get_engine().begin() as conn:
        repo.upsert_jquants_config(conn, fields)


def test_repo_upsert_is_single_row_and_idempotent(temp_db) -> None:
    """id 固定の 1 行運用。2 回 upsert しても行は 1 つで、最後の値が残る（embedding 同型）。"""
    _set({"api_key": "k1", "plan": "free"})
    _set({"api_key": "k2", "plan": "light"})
    with get_engine().connect() as conn:
        row = repo.get_jquants_config(conn)
    assert row is not None
    assert row["id"] == 1
    assert row["api_key"] == "k2"
    assert row["plan"] == "light"


def test_repo_partial_update_keeps_api_key(temp_db) -> None:
    """plan だけ渡せば api_key は据え置き（write-only の土台＝呼び出し側が空鍵を除外して渡す）。"""
    _set({"api_key": "secret", "plan": "free"})
    _set({"plan": "standard"})  # api_key を渡さない
    with get_engine().connect() as conn:
        row = repo.get_jquants_config(conn)
    assert row["api_key"] == "secret"
    assert row["plan"] == "standard"


def test_resolve_none_when_unset(temp_db) -> None:
    """未登録なら resolve は None・current_plan は "free"（最安全・ADR-061）。"""
    with get_engine().connect() as conn:
        assert resolve_jquants_config(conn) is None
        assert current_plan(conn) == "free"


def test_resolve_and_current_plan_when_set(temp_db) -> None:
    """登録済みなら resolve が {api_key, plan} を返し、plan は正規化される（大文字/空白吸収）。"""
    _set({"api_key": "abc", "plan": " Light "})
    with get_engine().connect() as conn:
        cfg = resolve_jquants_config(conn)
        assert cfg == {"api_key": "abc", "plan": "light"}
        assert current_plan(conn) == "light"


def test_get_endpoint_masks_and_defaults_free(client: Any) -> None:
    """GET /jquants/config: 未登録は configured=false・plan=free・api_key_masked 空。"""
    res = client.get("/jquants/config")
    assert res.status_code == 200
    body = res.json()
    assert body["configured"] is False
    assert body["has_api_key"] is False
    assert body["api_key_masked"] == ""
    assert body["plan"] == "free"


def test_put_then_get_masks_key_and_keeps_plan(client: Any) -> None:
    """PUT で api_key+plan を保存→GET はマスク済み・configured=true・plan を保持（ADR-061）。"""
    res = client.put("/jquants/config", json={"api_key": "supersecretkey", "plan": "light"})
    assert res.status_code == 200
    body = res.json()
    assert body["configured"] is True
    assert body["has_api_key"] is True
    assert body["api_key_masked"] == "…tkey"  # 末尾 4 桁のみ
    assert "supersecretkey" not in body["api_key_masked"]  # 生キーは返さない
    assert body["plan"] == "light"

    # 再 GET でも保持。
    got = client.get("/jquants/config").json()
    assert got["api_key_masked"] == "…tkey"
    assert got["plan"] == "light"


def test_put_empty_key_is_write_only_keeps_existing(client: Any) -> None:
    """PUT で api_key を空送信すると据え置き（write-only）。plan だけ更新できる。"""
    client.put("/jquants/config", json={"api_key": "firstkey1234", "plan": "free"})
    # api_key を送らず plan だけ変更。
    res = client.put("/jquants/config", json={"plan": "standard"})
    body = res.json()
    assert body["has_api_key"] is True  # 鍵は消えていない
    assert body["api_key_masked"] == "…1234"
    assert body["plan"] == "standard"
