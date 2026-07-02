"""EDINET DB（edinetdb.jp）接続設定の repo・resolver・REST を固定する（ADR-064）。

担保: 単一行 upsert の冪等・write-only（api_key 空送信は据え置き）・GET でのマスク・plan 中継・
未登録時の resolve None / current_plan="free"・plan_limits の plan 別値と未知 plan の free 倒し。
ネットに出ず一時 SQLite で回す（testing-strategy・jquants_config テストのミラー）。
"""

from __future__ import annotations

from typing import Any

from app.db import repo
from app.db.engine import get_engine
from app.services.edinetdb_config import (
    current_plan,
    plan_limits,
    resolve_edinetdb_config,
)


def _set(fields: dict[str, Any]) -> None:
    with get_engine().begin() as conn:
        repo.upsert_edinetdb_config(conn, fields)


def test_repo_upsert_is_single_row_and_idempotent(temp_db) -> None:
    """id 固定の 1 行運用。2 回 upsert しても行は 1 つで、最後の値が残る（jquants_config 同型）。"""
    _set({"api_key": "k1", "plan": "free"})
    _set({"api_key": "k2", "plan": "pro"})
    with get_engine().connect() as conn:
        row = repo.get_edinetdb_config(conn)
    assert row is not None
    assert row["id"] == 1
    assert row["api_key"] == "k2"
    assert row["plan"] == "pro"


def test_repo_partial_update_keeps_api_key(temp_db) -> None:
    """plan だけ渡せば api_key は据え置き（write-only の土台）。"""
    _set({"api_key": "secret", "plan": "free"})
    _set({"plan": "pro"})  # api_key を渡さない
    with get_engine().connect() as conn:
        row = repo.get_edinetdb_config(conn)
    assert row is not None
    assert row["api_key"] == "secret"
    assert row["plan"] == "pro"


def test_resolve_none_when_unset(temp_db) -> None:
    """未登録なら resolve は None・current_plan は "free"（最安全・ADR-064）。"""
    with get_engine().connect() as conn:
        assert resolve_edinetdb_config(conn) is None
        assert current_plan(conn) == "free"


def test_resolve_and_current_plan_when_set(temp_db) -> None:
    """登録済みなら resolve が {api_key, plan} を返し、plan は正規化される（大文字/空白吸収）。"""
    _set({"api_key": "abc", "plan": " Pro "})
    with get_engine().connect() as conn:
        cfg = resolve_edinetdb_config(conn)
        assert cfg == {"api_key": "abc", "plan": "pro"}
        assert current_plan(conn) == "pro"


def test_plan_limits_known_and_unknown() -> None:
    """plan 別のレート目安。free は日100/月600、未知 plan は free に倒す（最安全・ADR-064）。"""
    free = plan_limits("free")
    assert free.daily_budget == 100
    assert free.monthly_budget == 600
    pro = plan_limits("pro")
    assert pro.monthly_budget > free.monthly_budget
    # 未知 plan・None は free に倒す
    assert plan_limits("enterprise").monthly_budget == 600
    assert plan_limits(None).monthly_budget == 600


def test_get_endpoint_masks_and_defaults_free(client: Any) -> None:
    """GET /edinetdb/config: 未登録は configured=false・plan=free・api_key_masked 空。"""
    res = client.get("/edinetdb/config")
    assert res.status_code == 200
    body = res.json()
    assert body["configured"] is False
    assert body["has_api_key"] is False
    assert body["api_key_masked"] == ""
    assert body["plan"] == "free"


def test_put_then_get_masks_key_and_keeps_plan(client: Any) -> None:
    """PUT で api_key+plan を保存→GET はマスク済み・configured=true・plan を保持（ADR-064）。"""
    res = client.put("/edinetdb/config", json={"api_key": "edb_supersecret", "plan": "pro"})
    assert res.status_code == 200
    body = res.json()
    assert body["configured"] is True
    assert body["has_api_key"] is True
    assert body["api_key_masked"] == "…cret"  # 末尾 4 桁のみ
    assert "edb_supersecret" not in body["api_key_masked"]  # 生キーは返さない
    assert body["plan"] == "pro"

    got = client.get("/edinetdb/config").json()
    assert got["api_key_masked"] == "…cret"
    assert got["plan"] == "pro"


def test_put_empty_key_is_write_only_keeps_existing(client: Any) -> None:
    """PUT で api_key を空送信すると据え置き（write-only）。plan だけ更新できる。"""
    client.put("/edinetdb/config", json={"api_key": "edb_firstkey1", "plan": "free"})
    res = client.put("/edinetdb/config", json={"plan": "pro"})
    body = res.json()
    assert body["has_api_key"] is True  # 鍵は消えていない
    assert body["api_key_masked"] == "…key1"
    assert body["plan"] == "pro"


def test_edinetdb_test_unconfigured_returns_configured_false(client: Any) -> None:
    """POST /diagnostics/edinetdb-test: 未設定なら configured=false・ok=false（ネットに出ない）。"""
    res = client.post("/diagnostics/edinetdb-test")
    assert res.status_code == 200
    body = res.json()
    assert body["configured"] is False
    assert body["ok"] is False
