"""LLM プロバイダ・面別設定の REST ルータ（ADR-058・backend-router-pattern）。

設計の真実: docs/decisions.md ADR-058・docs/api.md「LLM 設定」節。

`/settings` の WebUI から複数 provider を登録し、面（chat/nightly/dossier/tagger）ごとに
provider と model を割り当てる。HTTP 入出力だけの薄い層で、解決ロジックは services/llm_config、
クエリは db/repo/llm_config が持つ（ADR-005/014）。秘密の api_key は GET では必ずマスクし、更新は
write-only（空送信は据え置き＝ADR-058 確定6）。provider は OpenAI 互換のみ（codex 経路は
ADR-073 で撤去）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path
from openai import OpenAIError
from pydantic import BaseModel
from sqlalchemy import Connection

from app.advisor.llm import get_client
from app.db import repo
from app.db.engine import get_conn, get_engine
from app.services.llm_config import FACES, describe_faces

router = APIRouter(tags=["llm-config"])


# ===== Pydantic 入出力（docs/api.md「LLM 設定」と 1:1） =====


class ProviderOut(BaseModel):
    """provider の公開表現。api_key は生で返さずマスクのみ（ADR-058 確定6）。"""

    id: int
    name: str
    base_url: str
    api_key_masked: str  # "…AB12"（末尾 4 桁）。空鍵は ""
    has_api_key: bool
    default_model: str


class ProviderCreate(BaseModel):
    name: str
    base_url: str
    api_key: str = ""  # 平文受領（ADR-001）。空可（ローカル LLM）
    default_model: str = ""


class ProviderUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None  # None/空文字＝据え置き（write-only・ADR-058 確定6）
    default_model: str | None = None


class FaceOut(BaseModel):
    face: str
    provider_id: int | None  # None=未設定 / >0=llm_providers.id
    provider_name: str | None  # 宙づりは None
    model: str
    reasoning_effort: str  # 空=既定 / minimal / low / medium / high（ADR-059）
    configured: bool  # resolve_face が通るか（=その面の LLM が動くか）


class FaceUpdate(BaseModel):
    provider_id: int | None  # None=未設定に戻す / >0=登録 provider
    model: str = ""
    reasoning_effort: str = ""  # 空=既定（ADR-059）


class ProviderTestResponse(BaseModel):
    ok: bool  # /v1/models に疎通できたか
    detail: str  # 人間向けメッセージ（成功＝モデル数／失敗＝エラー要旨）


class EmbeddingOut(BaseModel):
    """embedding 接続の公開表現（api_key はマスク・ADR-059）。"""

    base_url: str
    api_key_masked: str
    has_api_key: bool
    model: str
    dim: int
    configured: bool  # 3 キー揃いで意味検索が有効か


class EmbeddingUpdate(BaseModel):
    base_url: str | None = None
    api_key: str | None = None  # None/空文字＝据え置き（write-only）
    model: str | None = None
    dim: int | None = None


def _mask(api_key: str) -> str:
    """api_key をマスクする（GET で生キーを返さない・ADR-058 確定6）。"""
    if not api_key:
        return ""
    if len(api_key) <= 4:
        return "•" * len(api_key)
    return "…" + api_key[-4:]


def _to_provider_out(row: dict[str, object]) -> ProviderOut:
    key = str(row.get("api_key") or "")
    return ProviderOut(
        id=int(row["id"]),  # type: ignore[arg-type]
        name=str(row["name"]),
        base_url=str(row["base_url"]),
        api_key_masked=_mask(key),
        has_api_key=bool(key),
        default_model=str(row.get("default_model") or ""),
    )


# ===== providers CRUD =====


@router.get("/llm/providers", response_model=list[ProviderOut])
def list_providers(conn: Connection = Depends(get_conn)) -> list[ProviderOut]:
    """登録済み provider を一覧する（api_key はマスク・ADR-058）。"""
    return [_to_provider_out(row) for row in repo.list_providers(conn)]


@router.post("/llm/providers", response_model=ProviderOut)
def create_provider(body: ProviderCreate) -> ProviderOut:
    """provider を新規登録する（name 重複は 409・ADR-058）。"""
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name は必須です。")
    if not body.base_url.strip():
        raise HTTPException(status_code=422, detail="base_url は必須です。")
    with get_engine().begin() as conn:
        if repo.get_provider_by_name(conn, name) is not None:
            raise HTTPException(status_code=409, detail=f"provider 名 '{name}' は既に存在します。")
        pid = repo.insert_provider(
            conn,
            name=name,
            base_url=body.base_url.strip(),
            api_key=body.api_key,
            default_model=body.default_model.strip(),
        )
        row = repo.get_provider(conn, pid)
    assert row is not None  # 直前に insert した行は必ず存在する
    return _to_provider_out(row)


@router.put("/llm/providers/{provider_id}", response_model=ProviderOut)
def update_provider(
    body: ProviderUpdate,
    provider_id: int = Path(..., ge=1),
) -> ProviderOut:
    """provider を部分更新する（api_key は write-only＝空送信は据え置き・ADR-058 確定6）。"""
    with get_engine().begin() as conn:
        if repo.get_provider(conn, provider_id) is None:
            raise HTTPException(status_code=404, detail="provider が見つかりません。")
        fields: dict[str, object] = {}
        if body.name is not None:
            new_name = body.name.strip()
            if not new_name:
                raise HTTPException(status_code=422, detail="name は空にできません。")
            other = repo.get_provider_by_name(conn, new_name)
            if other is not None and int(other["id"]) != provider_id:
                raise HTTPException(
                    status_code=409, detail=f"provider 名 '{new_name}' は既に存在します。"
                )
            fields["name"] = new_name
        if body.base_url is not None:
            if not body.base_url.strip():
                raise HTTPException(status_code=422, detail="base_url は空にできません。")
            fields["base_url"] = body.base_url.strip()
        if body.default_model is not None:
            fields["default_model"] = body.default_model.strip()
        # api_key は非空文字列が来たときだけ更新（空文字・None は据え置き＝write-only）。
        if body.api_key:
            fields["api_key"] = body.api_key
        if fields:
            repo.update_provider(conn, provider_id, fields)
        row = repo.get_provider(conn, provider_id)
    assert row is not None
    return _to_provider_out(row)


@router.delete("/llm/providers/{provider_id}")
def delete_provider(provider_id: int = Path(..., ge=1)) -> dict[str, bool]:
    """provider を削除する。面が使用中なら 409 で拒否する（ADR-058 確定7）。"""
    with get_engine().begin() as conn:
        if repo.get_provider(conn, provider_id) is None:
            raise HTTPException(status_code=404, detail="provider が見つかりません。")
        used_by = repo.faces_using_provider(conn, provider_id)
        if used_by:
            raise HTTPException(
                status_code=409,
                detail=f"この provider は面 {', '.join(used_by)} が使用中です（先に割当変更を）。",
            )
        repo.delete_provider(conn, provider_id)
    return {"ok": True}


# ===== face_config（面→provider/model） =====


@router.get("/llm/faces", response_model=list[FaceOut])
def list_faces(conn: Connection = Depends(get_conn)) -> list[FaceOut]:
    """全面の現在割当を返す（未設定面も含め必ず全件・configured フラグ付き・ADR-058/062）。"""
    return [FaceOut(**row) for row in describe_faces(conn)]


@router.put("/llm/faces/{face}", response_model=FaceOut)
def update_face(
    body: FaceUpdate,
    face: str = Path(...),
) -> FaceOut:
    """面の provider/model 割当を更新する（None=未設定 / >0=登録 provider・ADR-058/068）。"""
    if face not in FACES:
        raise HTTPException(
            status_code=404, detail=f"未知の面: {face}（{', '.join(FACES)} のいずれか）"
        )
    with get_engine().begin() as conn:
        # provider_id は None（未設定に戻す）か実在 provider の id（>0）のみ許す（ADR-073）。
        if body.provider_id is not None:
            if body.provider_id <= 0 or repo.get_provider(conn, body.provider_id) is None:
                raise HTTPException(
                    status_code=422, detail=f"provider(id={body.provider_id}) が存在しません。"
                )
        repo.upsert_face(
            conn,
            face=face,
            provider_id=body.provider_id,
            model=body.model.strip(),
            reasoning_effort=body.reasoning_effort.strip() or None,
        )
        rows = describe_faces(conn)
    row = next(r for r in rows if r["face"] == face)
    return FaceOut(**row)


# ===== provider 疎通テスト（任意・diagnostics と同じ「200＋結果フラグ」流儀） =====


@router.post("/llm/providers/{provider_id}/test", response_model=ProviderTestResponse)
async def test_provider(provider_id: int = Path(..., ge=1)) -> ProviderTestResponse:
    """provider の /v1/models に疎通テストする（200＋結果フラグ・ADR-058）。

    失敗（鍵不正・base_url 誤り・/models 非対応）も例外にせず ok=false で返す（Web UI が表示）。
    """
    with get_engine().connect() as conn:
        prov = repo.get_provider(conn, provider_id)
    if prov is None:
        raise HTTPException(status_code=404, detail="provider が見つかりません。")
    client = get_client(str(prov["base_url"]), str(prov.get("api_key") or ""))
    try:
        page = await client.models.list()
        n = len(getattr(page, "data", []) or [])
        return ProviderTestResponse(ok=True, detail=f"疎通 OK（モデル {n} 件）")
    except OpenAIError as exc:
        return ProviderTestResponse(ok=False, detail=f"疎通失敗: {exc}")


# ===== embedding 接続（意味検索・単一行・ADR-059） =====


def _embedding_out(conn: Connection) -> EmbeddingOut:
    """embedding_config の現在値を表示用にまとめる（api_key はマスク・ADR-059）。"""
    row = repo.get_embedding_config(conn) or {}
    key = str(row.get("api_key") or "")
    base_url = str(row.get("base_url") or "")
    model = str(row.get("model") or "")
    return EmbeddingOut(
        base_url=base_url,
        api_key_masked=_mask(key),
        has_api_key=bool(key),
        model=model,
        dim=int(row.get("dim") or 0),
        configured=bool(base_url and key and model),
    )


@router.get("/llm/embedding", response_model=EmbeddingOut)
def get_embedding(conn: Connection = Depends(get_conn)) -> EmbeddingOut:
    """embedding 接続の現在値を返す（api_key はマスク・ADR-059）。"""
    return _embedding_out(conn)


@router.put("/llm/embedding", response_model=EmbeddingOut)
def update_embedding(body: EmbeddingUpdate) -> EmbeddingOut:
    """embedding 接続を部分更新する（api_key は write-only＝空送信は据え置き・ADR-059）。"""
    with get_engine().begin() as conn:
        fields: dict[str, object] = {}
        if body.base_url is not None:
            fields["base_url"] = body.base_url.strip()
        if body.model is not None:
            fields["model"] = body.model.strip()
        if body.dim is not None:
            fields["dim"] = body.dim
        if body.api_key:  # 非空文字列のときだけ更新（空・None は据え置き＝write-only）
            fields["api_key"] = body.api_key
        if fields:
            repo.upsert_embedding_config(conn, fields)
        out = _embedding_out(conn)
    return out


@router.post("/llm/embedding/test", response_model=ProviderTestResponse)
async def test_embedding() -> ProviderTestResponse:
    """embedding 接続に 1 件投げて疎通を確認する（200＋結果フラグ・ADR-059）。

    未設定（機能オフ）は ok=false で返す。失敗も例外にせず ok=false（Web UI が表示）。
    """
    from app.adapters.embedding import embed_texts, embedding_enabled

    if not embedding_enabled():
        return ProviderTestResponse(ok=False, detail="embedding 未設定（base_url/api_key/model）")
    try:
        vectors = await embed_texts(["ping"])
        dim = len(vectors[0]) if vectors else 0
        return ProviderTestResponse(ok=True, detail=f"疎通 OK（次元 {dim}）")
    except OpenAIError as exc:
        return ProviderTestResponse(ok=False, detail=f"疎通失敗: {exc}")
