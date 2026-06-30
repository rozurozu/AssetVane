"""知識カードの REST ルータ（ADR-062・backend-router-pattern）。

設計の真実: docs/decisions.md ADR-062（知識カード基盤＋「雑追加」リデザイン追補）。

`/cards` 管理画面から知識カードを追加・編集・削除する。**追加は本文（＋出所 URL）だけ**で行い、
追加時に同期で AI（`assist_card`＝triage 面）を走らせて title/when_to_apply/level を生成しつつ
verdict で status を振り分ける（rejected/to_core/needs_quant は自動・**active 候補は draft 留置＝
人間がワンクリック承認**＝ADR-009）。AI 未整形（面未設定/応答不正）でも本文は draft 保存し、行から
再整形できる。HTTP 入出力だけの薄い層で、クエリは db/repo/knowledge_cards、AI 整形は
advisor/card_triage、埋め込みは batch/jobs/embed_cards が持つ（ADR-005/014）。embedding BLOB は
返さない（repo が明示列で返す）。
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Connection

from app.db import repo
from app.db.engine import get_conn, get_engine

router = APIRouter(tags=["cards"])

CardLevel = Literal["stock", "sector", "market", "general"]
CardStatus = Literal["draft", "active", "needs_quant", "to_core", "rejected"]

# 既に計算済み（quant 実装済み）のシグナル種別。AI 審査が「active（既存値で成立）か needs_quant
# （新計算が要る）か」を判断する材料に渡す（signals.signal_type の実装済み集合）。
_IMPLEMENTED_SIGNAL_TYPES = ["momentum", "volume_spike", "ai_alpha", "lead_lag"]


class CardOut(BaseModel):
    """知識カードの公開表現（embedding BLOB は含めない・ADR-062）。"""

    id: int
    title: str
    body: str
    when_to_apply: str | None = None
    status: str
    level: str | None = None
    sector17_code: str | None = None
    theme: str | None = None
    linked_signal_type: str | None = None
    quant_note: str | None = None
    always_inject: bool = False
    # 重要度（retrieval ランク・注入順を distance/weight で重み付け・ADR-062 追補）
    weight: float = 1.0
    source: str | None = None
    # 追加時 AI 審査（assist_card）の判定理由（None=AI 未整形・ADR-062 追補）
    triage_reason: str | None = None
    embedded_at: str | None = None  # 埋め込み済みかの UI ヒント（None=未埋め込み）
    created_at: str | None = None
    updated_at: str | None = None


class CardCreateIn(BaseModel):
    """カード作成入力（ADR-062 追補・雑追加リデザイン）。本文＋出所 URL だけ。

    title/when_to_apply/level は追加時に AI（assist_card）が生成する（ユーザーは入力しない）。
    """

    body: str
    source: str | None = None  # 出所 URL（任意・記事の出典など）


class CardUpdateIn(BaseModel):
    title: str | None = None
    body: str | None = None
    when_to_apply: str | None = None
    level: CardLevel | None = None
    sector17_code: str | None = None
    theme: str | None = None
    source: str | None = None
    linked_signal_type: str | None = None
    quant_note: str | None = None
    always_inject: bool | None = None
    weight: float | None = Field(default=None, gt=0)  # 重要度（>0・ADR-062 追補）


class TriageOut(BaseModel):
    """AI 審査の結果（ADR-062）。"""

    verdict: str  # active/needs_quant/to_core/rejected
    reason: str
    quant_note: str | None = None
    linked_signal_type: str | None = None


class TriageResponse(BaseModel):
    """再整形エンドポイントの応答。triage=None は面未設定/応答不正でレビューできなかったとき。"""

    triage: TriageOut | None = None
    card: CardOut


def _card_out(row: dict[str, object]) -> CardOut:
    """repo の素 dict を CardOut へ（always_inject 0/1 → bool）。"""
    data = dict(row)
    data["always_inject"] = bool(data.get("always_inject"))
    return CardOut(**data)  # type: ignore[arg-type]


def _get_or_404(conn: Connection, card_id: int) -> dict[str, object]:
    row = repo.get_knowledge_card(conn, card_id)
    if row is None:
        raise HTTPException(status_code=404, detail="知識カードが見つかりません。")
    return row


async def _resolve_via_assist(body: str, *, title: str = "") -> dict[str, Any]:
    """本文を `assist_card`（triage 面）に通し、保存に使うフィールドへ解決する（ADR-062 追補）。

    返す dict＝title/when_to_apply/level/status/triage_reason/quant_note/linked_signal_type。
    - assist=None（面未設定/応答不正）→ AI 未整形。status=draft・reason None・
      title は入力のまま（再整形は既存 title 温存・create は空）。先頭切り出しはしない。
    - verdict=='active' → 人間承認待ちで status=draft 据え置き（active 化は activate 経由）。
    - 他（rejected/to_core/needs_quant）→ status=verdict を自動反映。
    LLM の await は書き込み tx の外で駆動する（C-6 の規律）。
    """
    from app.advisor.card_triage import assist_card

    result = await assist_card(
        body=body, title=title, existing_signal_types=_IMPLEMENTED_SIGNAL_TYPES
    )
    if result is None:
        return {
            "title": title.strip(),  # 再整形は既存 title 温存・create は空（切り出さない）
            "when_to_apply": None,
            "level": None,
            "status": "draft",
            "triage_reason": None,
            "quant_note": None,
            "linked_signal_type": None,
        }
    status = "draft" if result.verdict == "active" else result.verdict
    return {
        "title": result.title,  # AI 生成（空でも切り出さない）
        "when_to_apply": result.when_to_apply,
        "level": result.level,
        "status": status,
        "triage_reason": result.reason or None,
        "quant_note": result.quant_note,
        "linked_signal_type": result.linked_signal_type,
        "verdict": result.verdict,  # 応答（TriageOut）用
    }


@router.get("/cards", response_model=list[CardOut])
def list_cards(
    status: CardStatus | None = None,
    conn: Connection = Depends(get_conn),
) -> list[CardOut]:
    """知識カードを一覧する（status で絞り込み可・新しい順）。"""
    return [_card_out(r) for r in repo.list_knowledge_cards(conn, status=status)]


@router.get("/cards/{card_id}", response_model=CardOut)
def get_card(card_id: int, conn: Connection = Depends(get_conn)) -> CardOut:
    """知識カードを 1 件取得する。"""
    return _card_out(_get_or_404(conn, card_id))


@router.post("/cards", response_model=CardOut, status_code=201)
async def create_card(body: CardCreateIn) -> CardOut:
    """本文（＋source）だけでカードを作る。追加時に同期で AI が整形＋審査する（ADR-062 追補）。

    `assist_card` が title/when_to_apply/level を生成し verdict で status を決める（active 候補は
    draft 留置＝人間承認待ち）。AI 未整形でも本文は draft 保存（title 空）＝行から再整形できる。
    埋め込みは保存後 best-effort（await を書き込み tx 外で駆動・C-6）。
    """
    resolved = await _resolve_via_assist(body.body)
    card_id = repo.insert_knowledge_card(
        title=resolved["title"],
        body=body.body,
        when_to_apply=resolved["when_to_apply"],
        status=resolved["status"],
        level=resolved["level"],
        quant_note=resolved["quant_note"],
        linked_signal_type=resolved["linked_signal_type"],
        triage_reason=resolved["triage_reason"],
        source=body.source,
    )
    from app.batch.jobs.embed_cards import embed_card_best_effort

    embed_card_best_effort(card_id)
    with get_engine().connect() as conn:
        return _card_out(_get_or_404(conn, card_id))


# 埋め込み元（合成テキスト）を構成するフィールド。更新で変われば再埋め込みする（ADR-062 追補）。
_EMBED_SOURCE_FIELDS = ("title", "when_to_apply", "body")


@router.put("/cards/{card_id}", response_model=CardOut)
def update_card(card_id: int, body: CardUpdateIn) -> CardOut:
    """カードを部分更新する（埋め込み元が変われば再埋め込み・ADR-062 追補）。"""
    with get_engine().connect() as conn:
        _get_or_404(conn, card_id)
    values = body.model_dump(exclude_unset=True)
    if "always_inject" in values:
        values["always_inject"] = 1 if values["always_inject"] else 0
    repo.update_knowledge_card(card_id, values)
    if any(f in values for f in _EMBED_SOURCE_FIELDS):  # repo が embedding を無効化済み → 焼き直す
        from app.batch.jobs.embed_cards import embed_card_best_effort

        embed_card_best_effort(card_id)
    with get_engine().connect() as conn:
        return _card_out(_get_or_404(conn, card_id))


@router.delete("/cards/{card_id}", status_code=204)
def delete_card(card_id: int) -> None:
    """カードを削除する（無ければ 404）。"""
    with get_engine().connect() as conn:
        _get_or_404(conn, card_id)
    repo.delete_knowledge_card(card_id)


@router.post("/cards/{card_id}/assist", response_model=TriageResponse)
async def reassist_card_endpoint(card_id: int) -> TriageResponse:
    """既存カードを再整形する（AI 未整形の再試行＋編集後の再審査・ADR-062 追補）。

    保存済み body を `assist_card` に通し、title/when_to_apply/level と verdict→status・
    triage_reason を更新する（既存 title は assist に渡して温存/改善）。verdict=='active' は
    draft 据え置き（active 化は人間承認）。面未設定/応答不正なら triage=None（status 据え置き）。
    埋め込み元が変わるので best-effort で再埋め込みする。
    """
    with get_engine().connect() as conn:
        card = _get_or_404(conn, card_id)

    resolved = await _resolve_via_assist(str(card["body"]), title=str(card.get("title") or ""))
    verdict = resolved.get("verdict")  # None=AI 未整形（更新しない）

    if verdict is not None:
        repo.update_knowledge_card(
            card_id,
            {
                "title": resolved["title"],
                "when_to_apply": resolved["when_to_apply"],
                "level": resolved["level"],
            },
        )
        repo.set_knowledge_card_status(
            card_id,
            status=resolved["status"],
            quant_note=resolved["quant_note"],
            linked_signal_type=resolved["linked_signal_type"],
            reason=resolved["triage_reason"],
        )
        from app.batch.jobs.embed_cards import embed_card_best_effort

        embed_card_best_effort(card_id)

    with get_engine().connect() as conn:
        updated = _get_or_404(conn, card_id)
    triage_out = (
        TriageOut(
            verdict=str(verdict),
            reason=resolved["triage_reason"] or "",
            quant_note=resolved["quant_note"],
            linked_signal_type=resolved["linked_signal_type"],
        )
        if verdict is not None
        else None
    )
    return TriageResponse(triage=triage_out, card=_card_out(updated))


@router.post("/cards/{card_id}/activate", response_model=CardOut)
def activate_card(card_id: int) -> CardOut:
    """カードを active 化する（人間の最終承認＝本番助言に効く・ADR-009/062）。"""
    with get_engine().connect() as conn:
        _get_or_404(conn, card_id)
    repo.set_knowledge_card_status(card_id, status="active")
    with get_engine().connect() as conn:
        return _card_out(_get_or_404(conn, card_id))
