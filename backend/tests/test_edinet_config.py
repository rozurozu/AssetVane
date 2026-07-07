"""公式 EDINET 接続設定の repo・resolver・factory・REST を固定する（ADR-087）。

担保: 単一行 upsert の冪等・write-only（api_key 空送信は据え置き）・GET でのマスク・未登録時の
resolve None / build ファクトリの例外・configured=False の疎通・夜間 run() の未設定 skip。plan は
持たない（edinetdb_config テストのミラーから plan を落とした形）。ネットに出ず一時 SQLite で回す
（testing-strategy）。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.adapters.edinet import EdinetAdapterError
from app.batch.jobs import fetch_edinet_descriptions as edinet_job
from app.db import repo
from app.db.engine import get_engine
from app.services.edinet_config import build_edinet_adapter, resolve_edinet_config


def _set(fields: dict[str, Any]) -> None:
    with get_engine().begin() as conn:
        repo.upsert_edinet_config(conn, fields)


def test_repo_upsert_is_single_row_and_idempotent(temp_db) -> None:
    """id 固定の 1 行運用。2 回 upsert しても行は 1 つで、最後の値が残る（edinetdb 同型）。"""
    _set({"api_key": "k1"})
    _set({"api_key": "k2"})
    with get_engine().connect() as conn:
        row = repo.get_edinet_config(conn)
    assert row is not None
    assert row["id"] == 1
    assert row["api_key"] == "k2"


def test_repo_partial_update_keeps_api_key(temp_db) -> None:
    """api_key を渡さない upsert では既存 api_key が据え置き（write-only の土台）。"""
    _set({"api_key": "secret"})
    _set({"updated_at": "2026-07-07T00:00:00+00:00"})  # api_key を渡さない
    with get_engine().connect() as conn:
        row = repo.get_edinet_config(conn)
    assert row is not None
    assert row["api_key"] == "secret"


def test_resolve_none_when_unset(temp_db) -> None:
    """未登録なら resolve は None（＝未設定・段階C は静かに skip・ADR-087）。"""
    with get_engine().connect() as conn:
        assert resolve_edinet_config(conn) is None


def test_resolve_none_when_blank_key(temp_db) -> None:
    """空/空白のみの api_key も未設定として None（server_default="" の初期状態を含む）。"""
    _set({"api_key": "   "})
    with get_engine().connect() as conn:
        assert resolve_edinet_config(conn) is None


def test_resolve_when_set(temp_db) -> None:
    """登録済みなら resolve が {api_key} を返す（前後空白は落とす・plan は持たない）。"""
    _set({"api_key": "  edinet-real-key  "})
    with get_engine().connect() as conn:
        assert resolve_edinet_config(conn) == {"api_key": "edinet-real-key"}


def test_build_adapter_raises_when_unset(temp_db) -> None:
    """未設定の DB からファクトリを呼ぶと EdinetAdapterError（実取得系は握って skip・ADR-087）。"""
    with pytest.raises(EdinetAdapterError):
        with get_engine().connect() as conn:
            build_edinet_adapter(conn)


def test_build_adapter_returns_adapter_with_key(temp_db) -> None:
    """設定済みなら DB の api_key を持つ EdinetAdapter を返す（コンストラクタは非ネットワーク）。"""
    _set({"api_key": "abc123"})
    with get_engine().connect() as conn:
        adapter = build_edinet_adapter(conn)
    assert adapter._api_key == "abc123"


def test_get_endpoint_masks_and_unconfigured(client: Any) -> None:
    """GET /edinet/config: 未登録は configured=false・api_key_masked 空（plan 無し）。"""
    res = client.get("/edinet/config")
    assert res.status_code == 200
    body = res.json()
    assert body["configured"] is False
    assert body["has_api_key"] is False
    assert body["api_key_masked"] == ""
    assert "plan" not in body  # 公式 EDINET はプラン階層が無い


def test_put_then_get_masks_key(client: Any) -> None:
    """PUT で api_key を保存→GET はマスク済み・configured=true（生キーは返さない・ADR-087）。"""
    res = client.put("/edinet/config", json={"api_key": "edinet_supersecret"})
    assert res.status_code == 200
    body = res.json()
    assert body["configured"] is True
    assert body["has_api_key"] is True
    assert body["api_key_masked"] == "…cret"  # 末尾 4 桁のみ
    assert "edinet_supersecret" not in body["api_key_masked"]

    got = client.get("/edinet/config").json()
    assert got["api_key_masked"] == "…cret"
    assert got["has_api_key"] is True


def test_put_empty_key_is_write_only_keeps_existing(client: Any) -> None:
    """PUT で api_key を空送信すると据え置き（write-only）。"""
    client.put("/edinet/config", json={"api_key": "edinet_firstkey1"})
    res = client.put("/edinet/config", json={"api_key": ""})
    body = res.json()
    assert body["has_api_key"] is True  # 鍵は消えていない
    assert body["api_key_masked"] == "…key1"


def test_edinet_test_unconfigured_returns_configured_false(client: Any) -> None:
    """POST /diagnostics/edinet-test: 未設定なら configured=false・ok=false（ネットに出ない）。"""
    res = client.post("/diagnostics/edinet-test")
    assert res.status_code == 200
    body = res.json()
    assert body["configured"] is False
    assert body["ok"] is False


def test_nightly_run_skips_when_unconfigured(temp_db) -> None:
    """夜間 run() は公式 EDINET 未登録なら ok=True で静かに skip（段階C 機能オフ・ADR-087）。

    resolve が None のとき early return するので、_today_jst/クロール（ネット）へ進まない。
    """
    result = edinet_job.run()
    assert result.ok is True
    assert result.rows == 0
    assert "skip" in result.detail
