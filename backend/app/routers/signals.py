"""シグナル一覧の REST ルータ（Phase 1／docs/api.md §1・spec §5.1）。

GET /signals?date=&type=&limit=。シグナルは夜間バッチが事前計算済みで、API は読むだけ
（ADR-014: AI/API は計算しない。Python が焼いた事実を返すだけ）。`company_name` は
repo が `signals JOIN stocks` で補完する（行レベルに名前を焼かない＝spec B-6）。
"""

from __future__ import annotations

import datetime
import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Connection

from app.db import repo
from app.db.engine import get_conn
from app.services import freshness

router = APIRouter(tags=["signals"])

SignalType = Literal["momentum", "volume_spike", "ai_alpha", "lead_lag"]


class SignalPayload(BaseModel):
    """signals.payload（JSON）の表層。type 固有指標は quant が確定するため追加キーを許容する。"""

    # 追加キー許容（momentum/volume_spike の type 固有指標を素通しする＝spec §5.1）。
    model_config = ConfigDict(extra="allow")

    label: str | None = None  # 一覧の「シグナル」列の短文（quant が格納）
    change_5d: float | None = None  # 5 日騰落率（符号付き小数・quant が格納）


class Signal(BaseModel):
    code: str
    company_name: str | None = None  # signals JOIN stocks（ルータ補完・spec B-6）
    signal_type: SignalType
    score: float  # 0..1
    payload: SignalPayload


class SignalsResponse(BaseModel):
    date: str  # 実際に返した算出日（最新解決後）
    is_delayed: bool  # 横断の遅延フラグ（正本・spec §5.1）
    signals: list[Signal]  # score 降順


@router.get("/signals", response_model=SignalsResponse)
def list_signals(
    date: str | None = Query(default=None, description="算出日 YYYY-MM-DD。省略時は最新算出日"),
    type: SignalType | None = Query(default=None, description="シグナル種別。省略時は全 type"),
    limit: int = Query(default=100, ge=1, description="score 降順の上限"),
    conn: Connection = Depends(get_conn),
) -> SignalsResponse:
    """事前計算済みシグナルを返す（spec §5.1）。date 省略時は repo が最新算出日を解決する。"""
    # date 省略時の解決を先に行い、空でもトップの date を妥当な既定で埋められるようにする。
    resolved = date if date is not None else repo.get_latest_signal_date(conn, type)

    rows = repo.get_signals(conn, resolved, type, limit=limit)

    signals: list[Signal] = []
    for row in rows:
        # payload は repo から生の TEXT 文字列で来る（json.loads はルータの責務＝repo 契約）。
        raw: Any = row.get("payload")
        try:
            parsed = json.loads(raw) if raw else {}
        except (TypeError, ValueError) as exc:
            # 壊れた JSON は事前計算側のバグ。境界で 500 に翻訳して握りつぶさない。
            raise HTTPException(
                status_code=500,
                detail=f"signals.payload の JSON が不正です（code={row.get('code')}）。",
            ) from exc
        signals.append(
            Signal(
                code=row["code"],
                company_name=row.get("company_name"),
                signal_type=row["signal_type"],
                score=row["score"],
                payload=SignalPayload(**parsed),
            )
        )

    # signals が空でも妥当な既定を返す（date=今日・is_delayed=False）。
    # 空＝「該当 type のシグナルが 1 件も無い＝当日発火なし」であって、データが古いわけではない。
    today = datetime.date.today()
    if resolved is None:
        return SignalsResponse(date=today.isoformat(), is_delayed=False, signals=signals)

    # 鮮度判定は共有 freshness.is_delayed に一元化（ADR-071・暦日 7 日境界）。
    is_delayed = freshness.is_delayed(resolved, today)
    return SignalsResponse(date=resolved, is_delayed=is_delayed, signals=signals)
