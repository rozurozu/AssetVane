"""LLM 面別設定の解決サービス（ADR-058・backend-service-quant-pattern）。

設計の真実: docs/decisions.md ADR-058（LLM provider/model を env→DB+WebUI に移管）・ADR-018
（LLM 障害/未設定時のフォールバック）。

面（chat/nightly/dossier/tagger）→ {provider, base_url, api_key, model} を DB から解決する単一点。
repo（生クエリ）と engine/llm（呼び出し）の橋渡しで、未設定面の意味づけと例外を担う。codex は
llm_providers に行を持たない（鍵なし組み込み）ため provider_id=0 をセンチネルとして解決する。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection

from app.config import settings
from app.db import repo

# engine が source として渡す面（ADR-058 確定3）。tagger は theme_tagger/news_polarity が使う。
FACES: tuple[str, ...] = ("chat", "nightly", "dossier", "tagger")

# codex の仮想 provider_id（鍵なし組み込み・llm_providers に行を持たない＝ADR-058 確定1）。
CODEX_PROVIDER_ID = 0


class FaceNotConfiguredError(RuntimeError):
    """面に provider が未割当 / 鍵あり provider が宙づり / model 未指定（ADR-058・ADR-018）。

    engine が捕まえ、呼び出し元へ伝える: chat（router）は明示エラー、nightly/dossier は通知付き
    skip、tagger は沈黙 skip（ADR-058 確定8）。
    """


@dataclass(frozen=True)
class ResolvedFace:
    """面の解決結果（engine が provider 経路の振り分けと呼び出しに使う）。"""

    face: str
    provider: str  # "codex"（組み込み） / "openai"（OpenAI 互換・鍵あり）
    base_url: str | None  # codex のとき None
    api_key: str | None  # codex のとき None（鍵なし）
    model: str  # 実際に使う model（face.model 空なら provider 既定 or codex 既定）


def resolve_face(conn: Connection, face: str) -> ResolvedFace:
    """面 → ResolvedFace を解決する（ADR-058）。未設定/宙づり/model 欠落は例外。

    provider_id: NULL=未設定 / 0=codex / >0=llm_providers.id。codex は model 空なら
    settings.codex_model にフォールバック。鍵あり provider は model 空なら provider.default_model
    にフォールバックし、両方空なら FaceNotConfiguredError（model を確定できない）。
    """
    if face not in FACES:
        raise FaceNotConfiguredError(f"未知の面: {face}")

    row = repo.get_face(conn, face)
    if row is None or row["provider_id"] is None:
        raise FaceNotConfiguredError(f"面 '{face}' に provider が未割当（/settings で設定）")

    provider_id = int(row["provider_id"])
    model = (row["model"] or "").strip()

    if provider_id == CODEX_PROVIDER_ID:
        return ResolvedFace(
            face=face,
            provider="codex",
            base_url=None,
            api_key=None,
            model=model or settings.codex_model,
        )

    prov = repo.get_provider(conn, provider_id)
    if prov is None:
        raise FaceNotConfiguredError(
            f"面 '{face}' の provider(id={provider_id}) が削除済み（/settings で再割当）"
        )

    eff_model = model or (prov["default_model"] or "").strip()
    if not eff_model:
        raise FaceNotConfiguredError(
            f"面 '{face}' の model が未指定（face か provider 既定に model を設定）"
        )

    return ResolvedFace(
        face=face,
        provider="openai",
        base_url=prov["base_url"],
        api_key=prov["api_key"] or "",
        model=eff_model,
    )


def describe_faces(conn: Connection) -> list[dict[str, Any]]:
    """4 面の現在割当を表示用にまとめる（GET /llm/faces・ADR-058）。

    provider_name は codex なら "codex"、鍵あり provider なら名前（宙づりは None）。configured は
    resolve_face が通るか（=その面の LLM が動くか）。未設定面も行として 4 件必ず返す。
    """
    provider_names = {p["id"]: p["name"] for p in repo.list_providers(conn)}
    rows = {r["face"]: r for r in repo.list_faces(conn)}
    out: list[dict[str, Any]] = []
    for face in FACES:
        row = rows.get(face)
        provider_id = row["provider_id"] if row else None
        model = (row["model"] if row else "") or ""
        if provider_id == CODEX_PROVIDER_ID:
            provider_name: str | None = "codex"
        elif provider_id is not None:
            provider_name = provider_names.get(provider_id)
        else:
            provider_name = None
        try:
            resolve_face(conn, face)
            configured = True
        except FaceNotConfiguredError:
            configured = False
        out.append(
            {
                "face": face,
                "provider_id": provider_id,
                "provider_name": provider_name,
                "model": model,
                "configured": configured,
            }
        )
    return out
