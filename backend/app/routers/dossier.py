"""ドシエの REST ルータ（GET /dossiers/{code}・POST .../investigate・spec §5.2 / docs/api.md §5）。

設計の真実: docs/phase-specs/phase4-spec.md §5.2・ADR-020/ADR-014/ADR-005/L-23。

HTTP 入出力のみを担う薄い層（調査パイプライン本体は advisor/dossier.py の investigate_stock）。
- GET は `get_dossier`（stock_dossiers 1 行）＋ `list_dossier_sources`（台帳）を合成して返す。
  key_facts は生 TEXT なので router で json.loads する（壊れた JSON は 500 に翻訳）。
- POST は `with get_engine().begin() as conn:` で投資を束ね、investigate_stock(mode="chat") を
  同期実行（L-23・処理完了まで待つ）→ 最新ドシエを返す。書き手は FastAPI 1 プロセス（ADR-005）。
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import Connection

from app.advisor.dossier import investigate_stock
from app.db import repo
from app.db.engine import get_conn, get_engine

router = APIRouter(tags=["dossier"])


# ---------------------------------------------------------------------------
# Pydantic モデル（spec §5.2・TS 型 Dossier / DossierSource と 1:1）
# ---------------------------------------------------------------------------


class DossierSource(BaseModel):
    """ソース台帳の 1 件（spec §5.2・本文は持たず要約＋URL のみ＝ADR-020）。"""

    id: int
    source_type: str | None = None
    url: str
    title: str | None = None
    summary: str | None = None  # 短い要約（記事全文は保存しない）
    published_at: str | None = None


class Dossier(BaseModel):
    """ドシエ本体（spec §5.2・lib/api.ts Dossier と 1:1）。

    未調査の銘柄は summary_md="" ＋ sources=[] ＋ last_investigated_at=None で返す（GET の既定）。
    """

    code: str
    summary_md: str
    key_facts: dict[str, Any] | None = None  # 構造化（出所は Tool の事実・ADR-014）
    last_investigated_at: str | None = None
    updated_at: str | None = None
    sources: list[DossierSource] = []


class InvestigateResult(BaseModel):
    """POST /dossiers/{code}/investigate のレスポンス（調査後の最新ドシエ・spec §5.2）。"""

    dossier: Dossier


# ---------------------------------------------------------------------------
# 合成ヘルパ（dict → Pydantic・JSON パースは router の責務）
# ---------------------------------------------------------------------------


def _parse_key_facts(raw: Any) -> dict[str, Any] | None:
    """key_facts（生 TEXT）を dict に直す（None/空は None・壊れた JSON は 500 に翻訳）。"""
    if raw is None or raw == "":
        return None
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=500, detail="key_facts の JSON が不正です。") from exc
    return parsed if isinstance(parsed, dict) else None


def _build_dossier(conn: Connection, code: str) -> Dossier:
    """get_dossier ＋ list_dossier_sources を合成して Dossier を組む（spec §5.2）。

    未調査（get_dossier が None）は空ドシエ（summary_md="" ＋ sources=[]）を返す。
    sources は get_dossier では JOIN しないので別途取得して合成する（repo の契約）。
    """
    row = repo.get_dossier(conn, code)
    sources = [DossierSource(**s) for s in repo.list_dossier_sources(conn, code)]
    if row is None:
        return Dossier(code=code, summary_md="", key_facts=None, sources=sources)
    return Dossier(
        code=code,
        summary_md=row.get("summary_md") or "",
        key_facts=_parse_key_facts(row.get("key_facts")),
        last_investigated_at=row.get("last_investigated_at"),
        updated_at=row.get("updated_at"),
        sources=sources,
    )


# ---------------------------------------------------------------------------
# エンドポイント
# ---------------------------------------------------------------------------


@router.get("/dossiers/{code}", response_model=Dossier)
def get_dossier(code: str, conn: Connection = Depends(get_conn)) -> Dossier:
    """指定銘柄のドシエを返す（未調査でも空ドシエを 200 で返す・spec §5.2）。

    未調査の区別は last_investigated_at=None で表す（UI は「調査する」ボタンを出せる）。
    spec §5.2 は「404 または空ドシエ」の二択だが、frontend の DossierSection が常に描画
    （調査ボタン込み）するため空ドシエ（200）を採用する。
    """
    return _build_dossier(conn, code)


@router.post("/dossiers/{code}/investigate", response_model=InvestigateResult)
async def investigate(code: str) -> InvestigateResult:
    """銘柄を調査し（mode="chat"）、完了後に最新ドシエを返す（同期・L-23・spec §5.2）。

    investigate_stock はチャット「この銘柄調査して」と共用パイプライン（ADR-020）。
    書き込みは `with get_engine().begin()` で束ね、同一接続で合成まで読み切る（ADR-005）。
    """
    with get_engine().begin() as conn:
        await investigate_stock(conn, code, mode="chat")
        dossier = _build_dossier(conn, code)
    return InvestigateResult(dossier=dossier)
