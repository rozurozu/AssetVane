"""LLM プロバイダ・面別設定のクエリ（ADR-058・backend-repo-pattern）。

設計の真実: docs/decisions.md ADR-058・docs/data-model.md「LLM プロバイダ・面別設定」節。

provider（鍵あり・複数行）と face_config（chat/nightly/dossier/tagger の 4 行運用）を扱う。
codex は llm_providers に行を持たない（鍵なし組み込み・provider_id=0 を services/llm_config が
センチネルとして解決）。本モジュールは生の dict を返すだけで、未設定面の意味づけ・例外・マスクは
services/llm_config と router の責務（backend-repo / backend-router）。

[書き込みのトランザクション規律] write 関数は引数の `conn` 上で execute するだけで commit しない
（W2・advisor.py と同じ）。呼び出し側（router）が `with get_engine().begin() as conn:` で境界を
所有し、書き込み→読み戻しを 1 トランザクションに束ねる。read 関数は conn を受け commit しない。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, delete, insert, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.schema import llm_face_config, llm_providers

# ===== providers（鍵あり provider のレジストリ） =====


def list_providers(conn: Connection) -> list[dict[str, Any]]:
    """登録済み provider を id 昇順で全件返す（GET /llm/providers の素・ADR-058）。

    api_key は生のまま返す（マスクは router の責務＝GET では絶対に生キーを返さない）。
    """
    stmt = select(llm_providers).order_by(llm_providers.c.id)
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


def get_provider(conn: Connection, provider_id: int) -> dict[str, Any] | None:
    """provider 1 行を返す（無ければ None・ADR-058）。"""
    row = (
        conn.execute(select(llm_providers).where(llm_providers.c.id == provider_id))
        .mappings()
        .first()
    )
    return dict(row) if row else None


def get_provider_by_name(conn: Connection, name: str) -> dict[str, Any] | None:
    """name 一意制約の事前チェック用に同名 provider を返す（無ければ None・ADR-058）。"""
    row = conn.execute(select(llm_providers).where(llm_providers.c.name == name)).mappings().first()
    return dict(row) if row else None


def insert_provider(
    conn: Connection,
    *,
    name: str,
    base_url: str,
    api_key: str,
    default_model: str,
) -> int:
    """provider を 1 行 insert し、発行された id を返す（W2・commit しない・ADR-058）。"""
    now = datetime.now(UTC).isoformat()
    result = conn.execute(
        insert(llm_providers).values(
            name=name,
            base_url=base_url,
            api_key=api_key,
            default_model=default_model,
            created_at=now,
            updated_at=now,
        )
    )
    return int(result.inserted_primary_key[0])


def update_provider(conn: Connection, provider_id: int, fields: dict[str, Any]) -> None:
    """provider を部分更新する（fields の列だけ・W2・commit しない・ADR-058）。

    fields は変更したい列のみ（api_key 未送信＝据え置きは呼び出し側が fields から除外して渡す
    ＝write-only・ADR-058 確定6）。updated_at は未指定なら UTC now を入れる。
    """
    payload = {k: v for k, v in fields.items() if k not in ("id",)}
    payload.setdefault("updated_at", datetime.now(UTC).isoformat())
    conn.execute(update(llm_providers).where(llm_providers.c.id == provider_id).values(**payload))


def delete_provider(conn: Connection, provider_id: int) -> None:
    """provider を 1 行削除する（使用中チェックは呼び出し側＝409・W2・commit しない・ADR-058）。"""
    conn.execute(delete(llm_providers).where(llm_providers.c.id == provider_id))


# ===== face_config（面→provider/model の割当） =====


def list_faces(conn: Connection) -> list[dict[str, Any]]:
    """設定済みの面行を全件返す（未設定面はそもそも行が無い・ADR-058 確定4）。"""
    stmt = select(llm_face_config).order_by(llm_face_config.c.face)
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


def get_face(conn: Connection, face: str) -> dict[str, Any] | None:
    """面 1 行を返す（無ければ None＝未設定・resolve_face が ADR-018 で扱う）。"""
    row = (
        conn.execute(select(llm_face_config).where(llm_face_config.c.face == face))
        .mappings()
        .first()
    )
    return dict(row) if row else None


def upsert_face(conn: Connection, *, face: str, provider_id: int | None, model: str) -> None:
    """面の割当を upsert する（PK=face・provider_id=0 で codex・W2・commit しない・ADR-058）。"""
    now = datetime.now(UTC).isoformat()
    stmt = sqlite_insert(llm_face_config).values(
        face=face, provider_id=provider_id, model=model, updated_at=now
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["face"],
        set_={
            "provider_id": stmt.excluded.provider_id,
            "model": stmt.excluded.model,
            "updated_at": stmt.excluded.updated_at,
        },
    )
    conn.execute(stmt)


def faces_using_provider(conn: Connection, provider_id: int) -> list[str]:
    """指定 provider を参照している面名の一覧を返す（provider 削除の 409 判定・ADR-058 確定7）。"""
    stmt = (
        select(llm_face_config.c.face)
        .where(llm_face_config.c.provider_id == provider_id)
        .order_by(llm_face_config.c.face)
    )
    return [row[0] for row in conn.execute(stmt).all()]
