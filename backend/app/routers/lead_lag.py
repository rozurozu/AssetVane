"""日米業種リードラグの REST ルータ（GET /lead-lag・Phase 7・SIG-FIN-036-13）。

設計の真実: 論文 SIG-FIN-036-13・ADR-005（DB に触れるのは FastAPI だけ）・ADR-014（API は
計算しない＝夜間バッチ calc_lead_lag が焼いた signals を読むだけ）。

HTTP 入出力のみの薄い層。signals（signal_type='lead_lag'）の最新算出日分を score 降順で読み、
JP 業種ランキング＋model メタ（検証指標・遅延判定材料）を返す。frontend の lead-lag widget が
1:1 で消費する。計算は service/quant で済んでおり、ここは payload(JSON) を解いて詰め直すだけ。
"""

from __future__ import annotations

import datetime
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection

from app.db import repo
from app.db.engine import get_conn
from app.services.jquants_config import current_plan

router = APIRouter(tags=["lead-lag"])

# Free プランは 12 週間遅延（ADR-008）。as_of が today からこの日数以上前なら遅延扱いにする
# （signals ルータの境界より緩い＝lead-lag は遅延でも傾向は読めるため 30 日を境界にする）。
_DELAY_THRESHOLD_DAYS = 30


class LeadLagRankItem(BaseModel):
    """ランキング 1 行（JP 業種・score 降順で並ぶ）。"""

    code: str  # JP 業種 ETF コード（5桁の DB コード）
    label: str  # 業種和名（payload.label）
    score: float  # 横断 0..1 正規化スコア
    signal: float | None = None  # 生のシグナル値（payload.signal・縮退時 None）


class LeadLagMeta(BaseModel):
    """model メタ（検証指標＋遅延判定材料）。"""

    plan: str  # J-Quants プラン名（free / light）
    is_delayed: bool  # 低信頼バナー判定材料（free または as_of が大きく離れている）
    model_as_of: str | None = None  # シグナルの算出日（as_of）
    ic: float | None = None  # 横断 Spearman IC（検証）
    hit_rate: float | None = None  # LS 日次が正の割合（検証）
    window: int | None = None  # 推定ウィンドウ長
    k: int | None = None  # 抽出固有ベクトル数
    # 正則化係数。`lambda` は予約語なので Python 名は lambda_、JSON 入出力は "lambda" で揃える。
    lambda_: float | None = Field(default=None, alias="lambda")

    model_config = ConfigDict(populate_by_name=True)


class LeadLagResponse(BaseModel):
    """GET /lead-lag のレスポンス。台帳が空でも ranking=[] / as_of=None で 200。"""

    as_of: str | None = None
    ranking: list[LeadLagRankItem] = []
    meta: LeadLagMeta


def _parse_payload(raw: Any, code: str) -> dict[str, Any]:
    """signals.payload（生 TEXT）を dict にする。壊れていたら 500（事前計算側のバグ）。"""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=500,
            detail=f"lead_lag payload の JSON が不正です（code={code}）。",
        ) from exc
    return parsed if isinstance(parsed, dict) else {}


def _is_delayed(as_of: str | None, plan: str) -> bool:
    """plan=free、または as_of が today から閾値以上離れていれば遅延扱い（低信頼バナー材料）。

    plan は DB（jquants_config）から解決した契約プラン名（ADR-061）。Free は 12 週遅延のハード遅延。
    """
    if plan == "free":
        return True
    if not as_of:
        return False
    try:
        d = datetime.date.fromisoformat(as_of)
    except ValueError:
        return False
    return (datetime.date.today() - d).days >= _DELAY_THRESHOLD_DAYS


@router.get("/lead-lag", response_model=LeadLagResponse)
def get_lead_lag(conn: Connection = Depends(get_conn)) -> LeadLagResponse:
    """日米業種リードラグの最新ランキングと検証指標を返す（SIG-FIN-036-13・ADR-005/014）。

    signal_type='lead_lag' の最新算出日分を score 降順で読み、JP 業種ランキングに整える。
    meta はどの行の payload も同値（検証指標は model 単位）なので先頭行から拾う。台帳が空でも
    200 で ranking=[] / as_of=None（widget が壊れない）。
    """
    resolved = repo.get_latest_signal_date(conn, "lead_lag")
    rows = repo.get_signals(conn, resolved, "lead_lag", limit=100) if resolved else []

    ranking: list[LeadLagRankItem] = []
    head_payload: dict[str, Any] = {}
    for row in rows:
        payload = _parse_payload(row.get("payload"), row["code"])
        if not head_payload:
            head_payload = payload
        ranking.append(
            LeadLagRankItem(
                code=row["code"],
                label=payload.get("label") or row["code"],
                score=row["score"],
                signal=payload.get("signal"),
            )
        )

    plan = current_plan(conn)
    meta = LeadLagMeta(
        plan=plan,
        is_delayed=_is_delayed(resolved, plan),
        model_as_of=resolved,
        ic=head_payload.get("ic"),
        hit_rate=head_payload.get("hit_rate"),
        window=head_payload.get("window"),
        k=head_payload.get("k"),
        lambda_=head_payload.get("lambda"),
    )
    return LeadLagResponse(as_of=resolved, ranking=ranking, meta=meta)
