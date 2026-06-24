"""J-Quants 接続設定の REST ルータ（ADR-061・backend-router-pattern）。

設計の真実: docs/decisions.md ADR-061・docs/api.md「J-Quants 設定」節。

`/settings` の WebUI から J-Quants V2 の api_key と契約プラン名（free/light/standard/premium）を
編集する。HTTP 入出力だけの薄い層で、解決ロジックは services/jquants_config、クエリは
db/repo/jquants_config が持つ（ADR-005/014）。秘密の api_key は GET では必ずマスクし、更新は
write-only（空送信は据え置き＝ADR-058/059 と同方針）。疎通テストは既存の
POST /diagnostics/jquants-test を流用する（このルータには持たない）。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import Connection

from app.db import repo
from app.db.engine import get_conn, get_engine

router = APIRouter(tags=["jquants-config"])

# 許容プラン名（docs/jquants.md のプラン表・ADR-008/061）。UI のドロップダウンと 1:1。
JquantsPlan = Literal["free", "light", "standard", "premium"]


class JquantsConfigOut(BaseModel):
    """J-Quants 接続の公開表現（api_key はマスク・ADR-061）。"""

    api_key_masked: str  # "…AB12"（末尾 4 桁）。空鍵は ""
    has_api_key: bool
    plan: str  # free/light/standard/premium
    configured: bool  # api_key があり取得が動くか


class JquantsConfigUpdate(BaseModel):
    api_key: str | None = None  # None/空文字＝据え置き（write-only・ADR-061）
    plan: JquantsPlan | None = None


def _mask(api_key: str) -> str:
    """api_key をマスクする（GET で生キーを返さない・ADR-061・llm_config と同方針）。"""
    if not api_key:
        return ""
    if len(api_key) <= 4:
        return "•" * len(api_key)
    return "…" + api_key[-4:]


def _config_out(conn: Connection) -> JquantsConfigOut:
    """jquants_config の現在値を表示用にまとめる（api_key はマスク・ADR-061）。"""
    row = repo.get_jquants_config(conn) or {}
    key = str(row.get("api_key") or "")
    plan = (str(row.get("plan") or "").strip().lower()) or "free"
    return JquantsConfigOut(
        api_key_masked=_mask(key),
        has_api_key=bool(key),
        plan=plan,
        configured=bool(key),
    )


@router.get("/jquants/config", response_model=JquantsConfigOut)
def get_jquants_config(conn: Connection = Depends(get_conn)) -> JquantsConfigOut:
    """J-Quants 接続の現在値を返す（api_key はマスク・ADR-061）。"""
    return _config_out(conn)


@router.put("/jquants/config", response_model=JquantsConfigOut)
def update_jquants_config(body: JquantsConfigUpdate) -> JquantsConfigOut:
    """J-Quants 接続を部分更新する（api_key は write-only＝空送信は据え置き・ADR-061）。"""
    with get_engine().begin() as conn:
        fields: dict[str, object] = {}
        if body.plan is not None:
            fields["plan"] = body.plan
        if body.api_key:  # 非空文字列のときだけ更新（空・None は据え置き＝write-only）
            fields["api_key"] = body.api_key
        if fields:
            repo.upsert_jquants_config(conn, fields)
        out = _config_out(conn)
    return out
