"""知識カードの REST ルータ（ADR-062・backend-router-pattern）。

設計の真実: docs/decisions.md ADR-062（知識カード基盤）。

`/cards` 管理画面から知識カードを追加・編集・削除し、AI 審査（triage）で status を振り分け、人間が
active 化する（本番助言に効く操作は人間が最終承認・ADR-009）。HTTP 入出力だけの薄い層で、
クエリは db/repo/knowledge_cards、AI 審査は advisor/card_triage、埋め込みは batch/jobs/embed_cards
が持つ（ADR-005/014）。embedding BLOB は返さない（repo が明示列で返す）。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
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
    source: str | None = None
    embedded_at: str | None = None  # 埋め込み済みかの UI ヒント（None=未埋め込み）
    created_at: str | None = None
    updated_at: str | None = None


class CardCreateIn(BaseModel):
    title: str
    body: str
    when_to_apply: str | None = None
    level: CardLevel | None = None
    sector17_code: str | None = None
    theme: str | None = None
    source: str | None = None


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


class TriageOut(BaseModel):
    """AI 審査の結果（ADR-062）。"""

    verdict: str  # active/needs_quant/to_core/rejected
    reason: str
    quant_note: str | None = None
    linked_signal_type: str | None = None


class TriageResponse(BaseModel):
    """審査エンドポイントの応答。triage=None は面未設定/応答不正でレビューできなかったとき。"""

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
def create_card(body: CardCreateIn) -> CardOut:
    """カードを draft で作成し、when_to_apply を best-effort で即時埋め込む（ADR-062）。

    AI 審査は別口（POST /cards/{id}/triage）。作成は速く返し、埋め込み失敗は握る（夜間 embed_cards
    が拾う）。active 化は人間が承認する（POST /cards/{id}/activate）。
    """
    card_id = repo.insert_knowledge_card(
        title=body.title,
        body=body.body,
        when_to_apply=body.when_to_apply,
        status="draft",
        level=body.level,
        sector17_code=body.sector17_code,
        theme=body.theme,
        source=body.source,
    )
    # 保存後に await を tx 外で駆動して即時埋め込み（C-6 の規律・best-effort）。
    from app.batch.jobs.embed_cards import embed_card_best_effort

    embed_card_best_effort(card_id, body.when_to_apply)
    with get_engine().connect() as conn:
        return _card_out(_get_or_404(conn, card_id))


@router.put("/cards/{card_id}", response_model=CardOut)
def update_card(card_id: int, body: CardUpdateIn) -> CardOut:
    """カードを部分更新する（when_to_apply 変更時は再埋め込み・ADR-062）。"""
    with get_engine().connect() as conn:
        _get_or_404(conn, card_id)
    values = body.model_dump(exclude_unset=True)
    if "always_inject" in values:
        values["always_inject"] = 1 if values["always_inject"] else 0
    repo.update_knowledge_card(card_id, values)
    if "when_to_apply" in values:  # 更新で embedding を無効化済み → 即時で焼き直す（best-effort）
        from app.batch.jobs.embed_cards import embed_card_best_effort

        embed_card_best_effort(card_id, values.get("when_to_apply"))
    with get_engine().connect() as conn:
        return _card_out(_get_or_404(conn, card_id))


@router.delete("/cards/{card_id}", status_code=204)
def delete_card(card_id: int) -> None:
    """カードを削除する（無ければ 404）。"""
    with get_engine().connect() as conn:
        _get_or_404(conn, card_id)
    repo.delete_knowledge_card(card_id)


@router.post("/cards/{card_id}/triage", response_model=TriageResponse)
async def triage_card_endpoint(card_id: int) -> TriageResponse:
    """カードを AI 審査し status を振り分ける（ADR-062）。

    verdict が needs_quant/to_core/rejected ならその status を反映。'active' は人間承認を待つため
    status は draft のまま（linked_signal_type だけ反映）＝active 化は POST /cards/{id}/activate。
    面未設定/応答不正で審査できないときは triage=None（status 据え置き・ADR-018）。
    """
    with get_engine().connect() as conn:
        card = _get_or_404(conn, card_id)

    from app.advisor.card_triage import triage_card

    wta = card.get("when_to_apply")
    result = await triage_card(
        title=str(card["title"]),
        body=str(card["body"]),
        when_to_apply=str(wta) if wta else None,
        existing_signal_types=_IMPLEMENTED_SIGNAL_TYPES,
    )

    if result is not None:
        if result.verdict == "active":
            # active 化は人間承認（ADR-009）。draft のまま linked_signal_type だけ反映。
            repo.set_knowledge_card_status(
                card_id, status="draft", linked_signal_type=result.linked_signal_type
            )
        else:
            repo.set_knowledge_card_status(
                card_id,
                status=result.verdict,
                quant_note=result.quant_note,
                linked_signal_type=result.linked_signal_type,
            )

    with get_engine().connect() as conn:
        updated = _get_or_404(conn, card_id)
    triage_out = (
        TriageOut(
            verdict=result.verdict,
            reason=result.reason,
            quant_note=result.quant_note,
            linked_signal_type=result.linked_signal_type,
        )
        if result is not None
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
