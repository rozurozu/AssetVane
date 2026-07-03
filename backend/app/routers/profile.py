"""投資家プロファイルの REST ルータ（GET/PUT /profile ＋ pending 傾向メモ一覧・ADR-082）。

設計の真実: docs/decisions.md ADR-082。HTTP 入出力のみ（ロジックは repo/service）。active 文書の
取得/手編集（GET/PUT）は policy（advisor_state.py）と同型。夜バッチ profiler が起票した pending の
傾向メモ（proposals kind='profile_note'）の一覧は /profile/notes で返し、承認/却下は既存
/proposals/{id}/approve|reject（kind 非依存・承認で apply_profile_note が本文へ追記）を使う。
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import Connection

from app.db import repo
from app.db.engine import get_conn, get_engine

router = APIRouter(tags=["profile"])


class InvestorProfile(BaseModel):
    """投資家プロファイル（記述＝行動の癖・ADR-082）。body は散文 1 枚。"""

    body: str
    updated_at: str | None = None


class ProfileUpdate(BaseModel):
    """PUT /profile の入力（人間による手編集・全文置換）。"""

    body: str


class ProfileNote(BaseModel):
    """pending の傾向メモ（proposals kind='profile_note' の body を parse・承認待ち）。"""

    id: int
    text: str
    evidence: str
    created_date: str


def _parse_body(raw: object) -> dict[str, Any]:
    """proposals.body（JSON 文字列）を dict に直す（壊れ/非 dict は空・パースは router の責務）。"""
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


@router.get("/profile", response_model=InvestorProfile)
def get_profile(conn: Connection = Depends(get_conn)) -> InvestorProfile:
    """現在の投資家プロファイル（active 文書）を返す（未育成は空文字・ADR-082）。"""
    row = repo.get_investor_profile(conn)
    return InvestorProfile(body=str(row.get("body") or ""), updated_at=row.get("updated_at"))


@router.put("/profile", response_model=InvestorProfile)
def put_profile(req: ProfileUpdate) -> InvestorProfile:
    """投資家プロファイル本文を手編集で全文置換する（active は人間のみが育てる・ADR-009）。"""
    with get_engine().begin() as conn:
        repo.upsert_investor_profile(conn, req.body)
        row = repo.get_investor_profile(conn)
    return InvestorProfile(body=str(row.get("body") or ""), updated_at=row.get("updated_at"))


@router.get("/profile/notes", response_model=list[ProfileNote])
def get_profile_notes(conn: Connection = Depends(get_conn)) -> list[ProfileNote]:
    """pending の傾向メモ（profiler 起票）を返す。承認/却下は /proposals（ADR-082）。"""
    out: list[ProfileNote] = []
    for row in repo.list_proposals(conn, status="pending"):
        if row.get("kind") != "profile_note":
            continue
        body = _parse_body(row.get("body"))
        out.append(
            ProfileNote(
                id=int(row["id"]),
                text=str(body.get("text") or ""),
                evidence=str(body.get("evidence") or ""),
                created_date=str(row.get("created_date") or ""),
            )
        )
    return out
