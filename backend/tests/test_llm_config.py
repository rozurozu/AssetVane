"""LLM プロバイダ・面別設定の resolve_face と router を検証する（ADR-058）。

DB は temp_db / client（一時 SQLite・conftest が openai provider 1 行＋4 面を seed）。検証対象:
- resolve_face: openai 解決 / codex(provider_id=0) / 未設定→例外 / 宙づり→例外 / model 既定。
- router: GET マスク・POST 重複 409・PUT write-only（空キー据え置き）・DELETE 使用中 409・
  PUT /llm/faces で codex 割当。
ネットには出ない（疎通テスト endpoint は叩かない）。
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import delete, insert

from app.db import repo
from app.db.engine import get_engine
from app.db.schema import llm_face_config, llm_providers
from app.services import llm_config
from app.services.llm_config import FaceNotConfiguredError

# ===== resolve_face（DB 解決の単体） =====


def test_resolve_face_openai(temp_db: None) -> None:
    """seed 済みの面は provider="openai"・base_url/api_key/model が解決される。"""
    with get_engine().connect() as conn:
        rf = llm_config.resolve_face(conn, "chat")
    assert rf.provider == "openai"
    assert rf.base_url == "https://test.invalid/v1"
    assert rf.api_key == "test-key"
    assert rf.model == "test-model"


def test_resolve_face_codex(temp_db: None) -> None:
    """provider_id=0 は codex（base_url/api_key=None・model は face.model）に解決される。"""
    with get_engine().begin() as conn:
        repo.upsert_face(conn, face="nightly", provider_id=0, model="gpt-5.5")
    with get_engine().connect() as conn:
        rf = llm_config.resolve_face(conn, "nightly")
    assert rf.provider == "codex"
    assert rf.base_url is None
    assert rf.api_key is None
    assert rf.model == "gpt-5.5"


def test_resolve_face_unconfigured_raises(temp_db: None) -> None:
    """面の行が無い（未設定）と FaceNotConfiguredError（ADR-018）。"""
    with get_engine().begin() as conn:
        conn.execute(delete(llm_face_config).where(llm_face_config.c.face == "chat"))
    with get_engine().connect() as conn, pytest.raises(FaceNotConfiguredError):
        llm_config.resolve_face(conn, "chat")


def test_resolve_face_dangling_provider_raises(temp_db: None) -> None:
    """provider_id が削除済み provider を指す（宙づり）と FaceNotConfiguredError。"""
    with get_engine().begin() as conn:
        repo.upsert_face(conn, face="dossier", provider_id=9999, model="m")
    with get_engine().connect() as conn, pytest.raises(FaceNotConfiguredError):
        llm_config.resolve_face(conn, "dossier")


def test_resolve_face_model_falls_back_to_provider_default(temp_db: None) -> None:
    """face.model が空なら provider.default_model を使う。"""
    with get_engine().begin() as conn:
        pid = conn.execute(
            insert(llm_providers).values(
                name="p2", base_url="https://x/v1", api_key="k", default_model="default-m"
            )
        ).inserted_primary_key[0]
        repo.upsert_face(conn, face="tagger", provider_id=pid, model="")
    with get_engine().connect() as conn:
        rf = llm_config.resolve_face(conn, "tagger")
    assert rf.model == "default-m"


def test_describe_faces_returns_four(temp_db: None) -> None:
    """describe_faces は 4 面を必ず返し configured フラグを付ける。"""
    with get_engine().connect() as conn:
        faces = llm_config.describe_faces(conn)
    assert {f["face"] for f in faces} == {"chat", "nightly", "dossier", "tagger"}
    assert all(f["configured"] for f in faces)


# ===== router（client 経由） =====


def test_get_providers_masks_key(client: Any) -> None:
    """GET /llm/providers は api_key を生で返さずマスクする。"""
    res = client.get("/llm/providers")
    assert res.status_code == 200
    rows = res.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == "test-openai"
    assert row["has_api_key"] is True
    assert row["api_key_masked"].endswith("-key")
    assert "test-key" not in row["api_key_masked"]  # 生キーは絶対に返さない


def test_create_provider_duplicate_name_409(client: Any) -> None:
    """同名 provider の作成は 409。"""
    res = client.post(
        "/llm/providers",
        json={"name": "test-openai", "base_url": "https://y/v1", "api_key": "x"},
    )
    assert res.status_code == 409


def test_update_provider_api_key_write_only(client: Any) -> None:
    """空 api_key 送信は据え置き（write-only）。base_url だけ更新する。"""
    pid = client.get("/llm/providers").json()[0]["id"]
    res = client.put(
        f"/llm/providers/{pid}",
        json={"base_url": "https://changed/v1", "api_key": ""},
    )
    assert res.status_code == 200
    assert res.json()["base_url"] == "https://changed/v1"
    # api_key は据え置き（DB の値が消えていないこと）。
    with get_engine().connect() as conn:
        prov = repo.get_provider(conn, pid)
    assert prov is not None and prov["api_key"] == "test-key"


def test_delete_provider_in_use_409(client: Any) -> None:
    """4 面が使用中の provider は削除拒否（409）。"""
    pid = client.get("/llm/providers").json()[0]["id"]
    res = client.delete(f"/llm/providers/{pid}")
    assert res.status_code == 409


def test_assign_face_to_codex(client: Any) -> None:
    """PUT /llm/faces/{face} に provider_id=0 で codex を割り当てられる。"""
    res = client.put("/llm/faces/chat", json={"provider_id": 0, "model": "gpt-5.5"})
    assert res.status_code == 200
    body = res.json()
    assert body["provider_id"] == 0
    assert body["provider_name"] == "codex"
    assert body["model"] == "gpt-5.5"
    assert body["configured"] is True


def test_assign_face_unknown_provider_422(client: Any) -> None:
    """存在しない provider_id(>0) を面に割り当てると 422。"""
    res = client.put("/llm/faces/chat", json={"provider_id": 9999, "model": "m"})
    assert res.status_code == 422
