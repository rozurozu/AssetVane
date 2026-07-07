"""公式 EDINET（api.edinet-fsa.go.jp）接続設定の REST ルータ（ADR-087・backend-router-pattern）。

設計の真実: docs/decisions.md ADR-087・docs/api.md「EDINET（公式）設定」節。

`/settings` の WebUI から公式 EDINET の api_key（Subscription-Key）を編集する。HTTP 入出力だけの薄い
層で、解決は services/edinet_config、クエリは db/repo/edinet_config が持つ（ADR-005/014）。秘密の
api_key は GET でマスクし、更新は write-only（空送信は据え置き＝jquants/edinetdb_config 同方針）。
疎通テストは POST /diagnostics/edinet-test を流用する（このルータには持たない）。plan 概念は無い
（公式 EDINET は回数クォータ無し）。第三者 edinetdb.jp（/edinetdb/config）とは別系統。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import Connection

from app.db import repo
from app.db.engine import get_conn, get_engine

router = APIRouter(tags=["edinet-config"])


class EdinetConfigOut(BaseModel):
    """公式 EDINET 接続の公開表現（api_key はマスク・ADR-087）。"""

    api_key_masked: str  # "…AB12"（末尾 4 桁）。空鍵は ""
    has_api_key: bool
    configured: bool  # api_key があり段階C 取得が動くか


class EdinetConfigUpdate(BaseModel):
    api_key: str | None = None  # None/空文字＝据え置き（write-only・ADR-087）


def _mask(api_key: str) -> str:
    """api_key をマスクする（GET で生キーを返さない・ADR-087・edinetdb_config と同方針）。"""
    if not api_key:
        return ""
    if len(api_key) <= 4:
        return "•" * len(api_key)
    return "…" + api_key[-4:]


def _config_out(conn: Connection) -> EdinetConfigOut:
    """edinet_config の現在値を表示用にまとめる（api_key はマスク・ADR-087）。"""
    row = repo.get_edinet_config(conn) or {}
    key = str(row.get("api_key") or "")
    return EdinetConfigOut(
        api_key_masked=_mask(key),
        has_api_key=bool(key),
        configured=bool(key),
    )


@router.get("/edinet/config", response_model=EdinetConfigOut)
def get_edinet_config(conn: Connection = Depends(get_conn)) -> EdinetConfigOut:
    """公式 EDINET 接続の現在値を返す（api_key はマスク・ADR-087）。"""
    return _config_out(conn)


@router.put("/edinet/config", response_model=EdinetConfigOut)
def update_edinet_config(body: EdinetConfigUpdate) -> EdinetConfigOut:
    """公式 EDINET 接続を部分更新する（api_key は write-only＝空送信は据え置き・ADR-087）。"""
    with get_engine().begin() as conn:
        fields: dict[str, object] = {}
        if body.api_key:  # 非空文字列のときだけ更新（空・None は据え置き＝write-only）
            fields["api_key"] = body.api_key
        if fields:
            repo.upsert_edinet_config(conn, fields)
        out = _config_out(conn)
    return out
