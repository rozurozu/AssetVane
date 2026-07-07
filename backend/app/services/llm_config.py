"""LLM 面別設定の解決サービス（ADR-058・backend-service-quant-pattern）。

設計の真実: docs/decisions.md ADR-058（LLM provider/model を env→DB+WebUI に移管）・ADR-018
（LLM 障害/未設定時のフォールバック）。

面（chat/nightly/dossier/tagger/triage）→ {provider, base_url, api_key, model} を DB から解決する。
repo（生クエリ）と engine/llm（呼び出し）の橋渡しで、未設定面の意味づけと例外を担う。provider は
OpenAI 互換のみ（codex 経路は ADR-073 で撤去）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection

from app.db import repo

# engine が source として渡す面（ADR-058 確定3）。tagger は theme_tagger/news_polarity、
# triage は知識カードの AI 審査（card_triage・ADR-062）が使う（低頻度・結果が重いので独立面）。
# reviewer は経験蒸留（distill_experience・ADR-081）が使う（採点済み outcome→知識カード draft）。
# profiler は投資家プロファイル蒸留（distill_investor_profile・ADR-082）が使う（台帳→傾向メモ）。
# skeptic は提案前の反証（red_team_proposals・ADR-086）が使う（当夜 pending の buy/sell を独立面で
# 反証し body.skeptic に注記）。生成（nightly）と反証を別面にして red-team にする（別 model 推奨）。
FACES: tuple[str, ...] = (
    "chat",
    "nightly",
    "dossier",
    "tagger",
    "triage",
    "reviewer",
    "profiler",
    "skeptic",
)


class FaceNotConfiguredError(RuntimeError):
    """面に provider が未割当 / 鍵あり provider が宙づり / model 未指定（ADR-058・ADR-018）。

    engine が捕まえ、呼び出し元へ伝える: chat（router）は明示エラー、nightly/dossier は通知付き
    skip、tagger/triage は沈黙 skip（ADR-058 確定8・triage は ADR-062＝カードは draft 据え置き）。
    """


@dataclass(frozen=True)
class ResolvedFace:
    """面の解決結果（engine が provider 経路の呼び出しに使う）。"""

    face: str
    provider: str  # "openai"（OpenAI 互換・鍵あり。codex 経路は ADR-073 で撤去）
    base_url: str  # provider の base_url
    api_key: str  # provider の api_key（空可＝ローカル LLM）
    model: str  # 実際に使う model（face.model 空なら provider 既定）
    # 推論努力（空=既定・ADR-059）。resolve_face は常に明示渡しするが、既定 "" を置いて
    # 呼び出し側（テスト等）の構築を楽にする。
    reasoning_effort: str = ""


def resolve_face(conn: Connection, face: str) -> ResolvedFace:
    """面 → ResolvedFace を解決する（ADR-058）。未設定/宙づり/model 欠落は例外。

    provider_id: NULL=未設定 / >0=llm_providers.id。model 空なら provider.default_model に
    フォールバックし、両方空なら FaceNotConfiguredError（model を確定できない）。
    """
    if face not in FACES:
        raise FaceNotConfiguredError(f"未知の面: {face}")

    row = repo.get_face(conn, face)
    if row is None or row["provider_id"] is None:
        raise FaceNotConfiguredError(f"面 '{face}' に provider が未割当（/settings で設定）")

    provider_id = int(row["provider_id"])
    model = (row["model"] or "").strip()
    reasoning = (row.get("reasoning_effort") or "").strip()

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

    # openai 経路の reasoning は face の値のみ（空なら送らない・provider 既定なし・ADR-059）。
    return ResolvedFace(
        face=face,
        provider="openai",
        base_url=prov["base_url"],
        api_key=prov["api_key"] or "",
        model=eff_model,
        reasoning_effort=reasoning,
    )


def describe_faces(conn: Connection) -> list[dict[str, Any]]:
    """全面の現在割当を表示用にまとめる（GET /llm/faces・ADR-058）。

    provider_name は割当 provider の名前（未設定・宙づりは None）。configured は resolve_face が
    通るか（=その面の LLM が動くか）。未設定面も行として全件必ず返す。
    """
    provider_names = {p["id"]: p["name"] for p in repo.list_providers(conn)}
    rows = {r["face"]: r for r in repo.list_faces(conn)}
    out: list[dict[str, Any]] = []
    for face in FACES:
        row = rows.get(face)
        provider_id = row["provider_id"] if row else None
        model = (row["model"] if row else "") or ""
        reasoning = (row.get("reasoning_effort") if row else "") or ""
        provider_name: str | None
        if provider_id is not None:
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
                "reasoning_effort": reasoning,
                "configured": configured,
            }
        )
    return out


def resolve_embedding_config(conn: Connection) -> dict[str, Any] | None:
    """意味検索の embedding 接続を DB から解決する（ADR-059・ADR-045）。

    base_url / api_key / model の 3 キーが揃って初めて有効。1 つでも欠ければ None（静かに機能オフ・
    ADR-006/018）。戻り値は {base_url, api_key, model, dim}。dim は未設定で 0。
    """
    row = repo.get_embedding_config(conn)
    if row is None:
        return None
    base_url = (row.get("base_url") or "").strip()
    api_key = (row.get("api_key") or "").strip()
    model = (row.get("model") or "").strip()
    if not (base_url and api_key and model):
        return None
    return {"base_url": base_url, "api_key": api_key, "model": model, "dim": row.get("dim") or 0}
