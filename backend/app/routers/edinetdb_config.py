"""EDINET DB（edinetdb.jp）接続設定の REST ルータ（ADR-064・backend-router-pattern）。

設計の真実: docs/decisions.md ADR-064・docs/api.md「EDINET DB 設定」節。

`/settings` の WebUI から edinetdb.jp の api_key と契約プラン名（free/pro）を編集する。HTTP 入出力
だけの薄い層で、解決ロジックは services/edinetdb_config、クエリは db/repo/edinetdb_config が持つ
（ADR-005/014）。秘密の api_key は GET では必ずマスクし、更新は write-only（空送信は据え置き＝
jquants_config と同方針）。疎通テストは POST /diagnostics/edinetdb-test を流用する（このルータには
持たない）。公式 EDINET（DB の edinet_config・/edinet/config・ADR-087）とは別系統。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import Connection

from app.db import repo
from app.db.engine import get_conn, get_engine

router = APIRouter(tags=["edinetdb-config"])

# 許容プラン名（ADR-064・services/edinetdb_config._PLAN_LIMITS と 1:1）。UI のドロップダウンと一致。
EdinetDbPlan = Literal["free", "pro"]


class EdinetDbConfigOut(BaseModel):
    """EDINET DB 接続の公開表現（api_key はマスク・ADR-064）。"""

    api_key_masked: str  # "…AB12"（末尾 4 桁）。空鍵は ""
    has_api_key: bool
    plan: str  # free/pro
    configured: bool  # api_key があり #2 取得が動くか


class EdinetDbConfigUpdate(BaseModel):
    api_key: str | None = None  # None/空文字＝据え置き（write-only・ADR-064）
    plan: EdinetDbPlan | None = None


def _mask(api_key: str) -> str:
    """api_key をマスクする（GET で生キーを返さない・ADR-064・jquants_config と同方針）。"""
    if not api_key:
        return ""
    if len(api_key) <= 4:
        return "•" * len(api_key)
    return "…" + api_key[-4:]


def _config_out(conn: Connection) -> EdinetDbConfigOut:
    """edinetdb_config の現在値を表示用にまとめる（api_key はマスク・ADR-064）。"""
    row = repo.get_edinetdb_config(conn) or {}
    key = str(row.get("api_key") or "")
    plan = (str(row.get("plan") or "").strip().lower()) or "free"
    return EdinetDbConfigOut(
        api_key_masked=_mask(key),
        has_api_key=bool(key),
        plan=plan,
        configured=bool(key),
    )


@router.get("/edinetdb/config", response_model=EdinetDbConfigOut)
def get_edinetdb_config(conn: Connection = Depends(get_conn)) -> EdinetDbConfigOut:
    """EDINET DB 接続の現在値を返す（api_key はマスク・ADR-064）。"""
    return _config_out(conn)


@router.put("/edinetdb/config", response_model=EdinetDbConfigOut)
def update_edinetdb_config(body: EdinetDbConfigUpdate) -> EdinetDbConfigOut:
    """EDINET DB 接続を部分更新する（api_key は write-only＝空送信は据え置き・ADR-064）。"""
    with get_engine().begin() as conn:
        fields: dict[str, object] = {}
        if body.plan is not None:
            fields["plan"] = body.plan
        if body.api_key:  # 非空文字列のときだけ更新（空・None は据え置き＝write-only）
            fields["api_key"] = body.api_key
        if fields:
            repo.upsert_edinetdb_config(conn, fields)
        out = _config_out(conn)
    return out
