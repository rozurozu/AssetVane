"""ドシエの REST ルータ（GET /dossiers/{code}・POST .../investigate・spec §5.2 / docs/api.md §5）。

設計の真実: docs/phase-specs/phase4-spec.md §5.2・ADR-020/ADR-014/ADR-005/ADR-044/L-23。

HTTP 入出力のみを担う薄い層（調査パイプライン本体は advisor/dossier.py の investigate_stock）。
- GET は `get_dossier`（stock_dossiers 1 行）＋ `list_news`（統合コーパス news の銘柄層）を
  合成して返す。news 列（source/fetched_at 等）は DossierSource の既存フィールド名へマップする
  （ADR-044 で台帳を統合した後も frontend が読む API レスポンス形は不変＝source→source_type）。
  key_facts は生 TEXT なので router で json.loads する（壊れた JSON は 500 に翻訳）。
- POST は `with get_engine().begin() as conn:` で投資を束ね、investigate_stock を
  同期実行（L-23・処理完了まで待つ）→ 最新ドシエを返す。書き手は FastAPI 1 プロセス（ADR-005）。
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import Connection

from app.advisor import dossier_progress
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
    # 今この銘柄を調査中か（ADR-076・プロセスメモリの dossier_progress 由来）。frontend は
    # リロード時にこれを読んで「調査中…」表示を復元し、true の間は完了をポーリングで検知する。
    investigating: bool = False


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


def _to_source(row: dict[str, Any]) -> DossierSource:
    """統合コーパス news の 1 行を DossierSource にマップする（ADR-044・API 形は不変）。

    台帳統合（ADR-044）で列名が変わった（source_type→source）ため、frontend が読む既存
    フィールド名（source_type）へ明示的に詰め替える。news の余分なタグ（level/code/
    sector17_code/category/fetched_at/extraction_status）は DossierSource に持たせない
    （spec §5.2 のソース台帳契約を変えない）。
    """
    return DossierSource(
        id=row["id"],
        source_type=row.get("source"),  # 旧 source_type の住所（ADR-044 で source へ改名）
        url=row["url"],
        title=row.get("title"),
        summary=row.get("summary"),
        published_at=row.get("published_at"),
    )


def _build_dossier(conn: Connection, code: str) -> Dossier:
    """get_dossier ＋ 統合コーパス news（銘柄層）を合成して Dossier を組む（spec §5.2・ADR-044）。

    未調査（get_dossier が None）は空ドシエ（summary_md="" ＋ sources=[]）を返す。
    sources は get_dossier では JOIN しないので別途取得して合成する（repo の契約）。
    ソースは統合コーパス news の level="stock"＋code 層から引く（list_news は published_at 降順）。
    """
    row = repo.get_dossier(conn, code)
    sources = [_to_source(s) for s in repo.list_news(conn, level="stock", code=code)]
    # 進行状態はプロセスメモリの真実（ADR-076）。未調査（row is None）でも調査中はありうる
    # （初回調査の実行中はまだドシエ行が無い）ので、両分岐で investigating を載せる。
    investigating = dossier_progress.is_investigating(code)
    if row is None:
        return Dossier(
            code=code, summary_md="", key_facts=None, sources=sources, investigating=investigating
        )
    return Dossier(
        code=code,
        summary_md=row.get("summary_md") or "",
        key_facts=_parse_key_facts(row.get("key_facts")),
        last_investigated_at=row.get("last_investigated_at"),
        updated_at=row.get("updated_at"),
        sources=sources,
        investigating=investigating,
    )


# ---------------------------------------------------------------------------
# エンドポイント
# ---------------------------------------------------------------------------


@router.get("/dossiers/{code}", response_model=Dossier)
def get_dossier(code: str, conn: Connection = Depends(get_conn)) -> Dossier:
    """指定銘柄のドシエを返す（未調査でも空ドシエを 200 で返す・spec §5.2）。

    未調査の区別は last_investigated_at=None で表す（UI は「調査する」ボタンを出せる）。
    当初 spec §5.2 は「404 または空ドシエ」の二択を残していたが、frontend の DossierSection が
    未調査銘柄でも常に描画（調査ボタン込み）する設計と素直に噛み合う 200 固定で確定した
    （2026-06-08 判断・404 は採らない）。
    """
    return _build_dossier(conn, code)


@router.post("/dossiers/{code}/investigate", response_model=InvestigateResult)
async def investigate(code: str) -> InvestigateResult:
    """銘柄を調査し、完了後に最新ドシエを返す（同期・L-23・spec §5.2）。

    investigate_stock はチャット「この銘柄調査して」と共用パイプライン（ADR-020・取得手段は
    httpx 一本に統一したため mode は廃止）。書き込みは `with get_engine().begin()` で束ね、
    同一接続で合成まで読み切る（ADR-005）。
    """
    with get_engine().begin() as conn:
        await investigate_stock(conn, code)
        dossier = _build_dossier(conn, code)
    return InvestigateResult(dossier=dossier)
