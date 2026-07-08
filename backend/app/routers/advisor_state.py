"""AI Advisor 状態の REST ルータ（policy / journal / proposals・spec §8.2）。

設計の真実: docs/phase-specs/phase3-spec.md §8.2・ADR-013/018/029・決定4/B-8。

HTTP 入出力のみを担う（ロジックは service.py / repo.py）。読み取りは `Depends(get_conn)`、
書き込みは `with get_engine().begin() as conn:` で原子化する（既存 portfolio.py/assets.py の流儀）。

このルータ層の責務:
- policy の DB 形変換は services/policy.py の単一点に委譲する（書き=encode_policy_field・
  読みの正規化=normalize_policy_row・ADR-013）。レスポンス整形（core/rationale 分離）の
  読み変換ヘルパ（_as_json_obj/_as_json_list）は本ルータに残す。
- policy を core（最適化に効く構造化コア）と rationale（理念テキスト）に分離して返す（§8.2）。
- proposals の承認/却下を service.resolve_proposal に委譲し、例外を 404/409 に翻訳する。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import Connection

from app.advisor import service
from app.db import repo
from app.db.engine import get_conn, get_engine
from app.services import policy as policy_service

router = APIRouter(tags=["advisor-state"])


# ---------------------------------------------------------------------------
# Pydantic モデル（spec §8.2・TS 型と 1:1）
# ---------------------------------------------------------------------------


class PolicyCore(BaseModel):
    """policy の構造化コア（最適化に効く定量レバー・spec §8.2）。比率は 0..1。"""

    risk_tolerance: str | None = None
    time_horizon: str | None = None
    target_cash_ratio: float | None = None
    max_position_weight: float | None = None
    sector_caps: dict[str, float] = {}
    target_return: float | None = None
    no_leverage: bool = False
    exclusions: list[str] = []


class Policy(BaseModel):
    """GET/PUT /policy のレスポンス（core と rationale を分離・spec §8.2）。"""

    core: PolicyCore
    rationale: str | None = None
    updated_at: str | None = None


class PolicyCoreUpdate(BaseModel):
    """PUT /policy の core 部分更新（全フィールド任意・spec §8.2）。"""

    risk_tolerance: str | None = None
    time_horizon: str | None = None
    target_cash_ratio: float | None = None
    max_position_weight: float | None = None
    sector_caps: dict[str, float] | None = None
    target_return: float | None = None
    no_leverage: bool | None = None
    exclusions: list[str] | None = None


class PolicyUpdate(BaseModel):
    """PUT /policy のリクエスト（spec §8.2）。core 変更は承認制相当の即時反映入口。"""

    core: PolicyCoreUpdate | None = None
    rationale: str | None = None


class JournalEntry(BaseModel):
    """advisor_journal の 1 件（spec §8.2・ADR-029）。一覧では situation_briefing を省く。"""

    id: int
    date: str
    source: str  # 'nightly' / 'chat'
    observations: str | None = None
    proposal: str | None = None
    proposed_policy_change: dict[str, Any] | None = None
    policy_snapshot: dict[str, Any] | None = None
    situation_briefing: dict[str, Any] | None = None  # 詳細 GET /journal/{id} のみ載る
    llm_model: str | None = None
    created_at: str | None = None


class JournalResponse(BaseModel):
    """GET /journal のレスポンス（spec §8.2）。"""

    entries: list[JournalEntry]


class Proposal(BaseModel):
    """proposals の 1 件（spec §8.2・決定4）。"""

    id: int
    created_date: str
    kind: str
    body: dict[str, Any] | None = None
    rationale: str | None = None
    status: str
    outcome: str | None = None
    resolved_at: str | None = None
    journal_id: int | None = None
    depends_on: int | None = None


class ProposalsResponse(BaseModel):
    """GET /proposals のレスポンス（spec §8.2）。"""

    proposals: list[Proposal]


class ResolveBody(BaseModel):
    """approve/reject のリクエストボディ（spec §8.2）。"""

    outcome: str | None = None


class ResolveResult(BaseModel):
    """approve/reject のレスポンス（spec §8.2）。"""

    proposal: Proposal


# ---------------------------------------------------------------------------
# 変換ヘルパ（int↔bool・JSON↔型・ルータ層の責務・spec §8.2）
# ---------------------------------------------------------------------------


def _as_json_obj(raw: Any) -> dict[str, Any] | None:
    """JSON 文字列 or dict を dict にする（壊れていたら None）。"""
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _as_json_list(raw: Any) -> list[Any]:
    """JSON 文字列 or list を list にする（壊れ・空は []）。"""
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _row_to_policy(row: dict[str, Any]) -> Policy:
    """services.policy.get_policy の生 dict を core/rationale 分離の Policy に整形（§8.2）。

    no_leverage は int→bool、sector_caps/exclusions は JSON 文字列→型に直す
    （DEFAULT_POLICY 由来なら既に dict/list なのでそのまま使える）。
    """
    core = PolicyCore(
        risk_tolerance=row.get("risk_tolerance"),
        time_horizon=row.get("time_horizon"),
        target_cash_ratio=row.get("target_cash_ratio"),
        max_position_weight=row.get("max_position_weight"),
        sector_caps={k: float(v) for k, v in (_as_json_obj(row.get("sector_caps")) or {}).items()},
        target_return=row.get("target_return"),
        no_leverage=bool(row.get("no_leverage")),
        exclusions=[str(x) for x in _as_json_list(row.get("exclusions"))],
    )
    return Policy(
        core=core,
        rationale=row.get("rationale"),
        updated_at=row.get("updated_at"),
    )


def _journal_row_to_entry(row: dict[str, Any], *, with_briefing: bool) -> JournalEntry:
    """advisor_journal の生 dict を JournalEntry に整形（JSON 列を型に直す・§8.2）。"""
    return JournalEntry(
        id=int(row["id"]),
        date=row["date"],
        source=row.get("source") or "nightly",
        observations=row.get("observations"),
        proposal=row.get("proposal"),
        proposed_policy_change=_as_json_obj(row.get("proposed_policy_change")),
        policy_snapshot=_as_json_obj(row.get("policy_snapshot")),
        situation_briefing=_as_json_obj(row.get("situation_briefing")) if with_briefing else None,
        llm_model=row.get("llm_model"),
        created_at=row.get("created_at"),
    )


def _proposal_row_to_model(row: dict[str, Any]) -> Proposal:
    """proposals の生 dict を Proposal に整形（body JSON を型に直す・§8.2）。"""
    return Proposal(
        id=int(row["id"]),
        created_date=row["created_date"],
        kind=row["kind"],
        body=_as_json_obj(row.get("body")),
        rationale=row.get("rationale"),
        status=row["status"],
        outcome=row.get("outcome"),
        resolved_at=row.get("resolved_at"),
        journal_id=row.get("journal_id"),
        depends_on=row.get("depends_on"),
    )


# ---------------------------------------------------------------------------
# /policy
# ---------------------------------------------------------------------------


@router.get("/policy", response_model=Policy)
def get_policy(conn: Connection = Depends(get_conn)) -> Policy:
    """現在の policy を core/rationale 分離で返す（未設定でも DEFAULT がマージされる・§8.2）。"""
    row = policy_service.get_policy(conn)
    return _row_to_policy(row)


@router.put("/policy", response_model=Policy)
def put_policy(req: PolicyUpdate) -> Policy:
    """policy を更新する（チャット承認後と Policy 画面直接編集の両入口・§8.2）。

    core 変更があれば当日 advisor_journal に policy_snapshot を残す（ADR-013）。
    DB 形変換（sector_caps/exclusions の json.dumps・no_leverage の 0/1）は提案承認と
    共通の encode_policy_field に委ねる（services/policy.py が単一点・ADR-013）。
    """
    fields: dict[str, Any] = {}
    has_core_change = False
    if req.core is not None:
        core_dump = req.core.model_dump(exclude_unset=True)
        for key, value in core_dump.items():
            fields[key] = policy_service.encode_policy_field(key, value)
        has_core_change = bool(core_dump)
    if req.rationale is not None:
        fields["rationale"] = req.rationale

    with get_engine().begin() as conn:
        if fields:
            repo.upsert_policy(conn, fields)
        # core 変更があれば当日 journal に snapshot を残す（理念のみの更新では残さない・§6.5）。
        # snapshot は dumps 前に JSON 列を型へ直す（二重エンコード防止・ADR-013）。
        if has_core_change:
            updated = repo.get_policy(conn)
            snapshot = (
                json.dumps(policy_service.normalize_policy_row(updated), ensure_ascii=False)
                if updated is not None
                else None
            )
            repo.insert_journal(
                conn,
                date=datetime.now(UTC).strftime("%Y-%m-%d"),
                source="chat",
                observations="方針を更新",
                policy_snapshot=snapshot,
            )
        row = policy_service.get_policy(conn)
    return _row_to_policy(row)


# ---------------------------------------------------------------------------
# /journal
# ---------------------------------------------------------------------------


@router.get("/journal", response_model=JournalResponse)
def get_journal(
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
    conn: Connection = Depends(get_conn),
) -> JournalResponse:
    """advisor_journal を date 降順で返す（situation_briefing は載せない・§8.2）。"""
    rows = repo.list_journal(conn, from_=from_, to=to)
    return JournalResponse(
        entries=[_journal_row_to_entry(r, with_briefing=False) for r in rows],
    )


@router.get("/journal/{journal_id}", response_model=JournalEntry)
def get_journal_detail(
    journal_id: int,
    conn: Connection = Depends(get_conn),
) -> JournalEntry:
    """advisor_journal の 1 件を situation_briefing 込みで返す（§8.2）。"""
    row = repo.get_journal(conn, journal_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"投資日記 {journal_id} は存在しません。")
    return _journal_row_to_entry(row, with_briefing=True)


# ---------------------------------------------------------------------------
# /proposals
# ---------------------------------------------------------------------------


@router.get("/proposals", response_model=ProposalsResponse)
def get_proposals(
    status: str | None = Query(default=None),
    conn: Connection = Depends(get_conn),
) -> ProposalsResponse:
    """proposals を created_date 降順で返す（status 指定で絞り込み・§8.2）。"""
    rows = repo.list_proposals(conn, status=status)
    return ProposalsResponse(proposals=[_proposal_row_to_model(r) for r in rows])


def _resolve(proposal_id: int, *, decision: str, outcome: str | None) -> ResolveResult:
    """approve/reject の共通処理（service.resolve_proposal に委譲・例外を HTTP に翻訳・§8.2）。"""
    try:
        with get_engine().begin() as conn:
            service.resolve_proposal(
                conn,
                proposal_id,
                decision=decision,  # type: ignore[arg-type]
                outcome=outcome,
            )
            row = repo.get_proposal(conn, proposal_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        # depends_on 未承認など（承認順制御・決定4/B-8）。
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail=f"提案 {proposal_id} は存在しません。")
    return ResolveResult(proposal=_proposal_row_to_model(row))


@router.post("/proposals/{proposal_id}/approve", response_model=ResolveResult)
def approve_proposal(proposal_id: int, body: ResolveBody | None = None) -> ResolveResult:
    """提案を承認する（policy_change なら policy 適用・depends_on 未承認は 409・§8.2）。"""
    outcome = body.outcome if body else None
    return _resolve(proposal_id, decision="approved", outcome=outcome)


@router.post("/proposals/{proposal_id}/reject", response_model=ResolveResult)
def reject_proposal(proposal_id: int, body: ResolveBody | None = None) -> ResolveResult:
    """提案を却下する（status のみ rejected に遷移・§8.2）。"""
    outcome = body.outcome if body else None
    return _resolve(proposal_id, decision="rejected", outcome=outcome)


# ---------------------------------------------------------------------------
# /advisor/turns（AI Advisor 判断軌跡の観測層・ADR-092）
# ---------------------------------------------------------------------------


class TurnToolCall(BaseModel):
    """1 ターンで呼んだ Tool の 1 件（tool_sequence の要素・結果値なし＝ADR-025）。"""

    name: str
    args: dict[str, Any] = {}


class TurnItem(BaseModel):
    """advisor_turns の 1 行（ADR-092）。列＋tool_sequence 由来の read-time 導出フラグ。"""

    id: int
    created_at: str | None = None
    source: str  # 'chat'/'nightly'/'reviewer'/'profiler'/'skeptic'
    model: str | None = None
    tool_sequence: list[TurnToolCall] = []
    n_rounds: int
    truncated: bool
    called_propose_trade: bool
    propose_trade_disciplined: bool | None = None  # None=非該当（propose_trade を呼んでいない）
    # 表示専用の導出フラグ（列にせず tool_sequence を走査＝read-time・ADR-092）。
    called_submit_journal: bool
    called_submit_notable: bool


class TurnsSummaryRow(BaseModel):
    """面別の判断軌跡サマリ（aggregate_turns と 1:1・ADR-092）。"""

    source: str
    n_turns: int
    avg_rounds: float | None = None
    truncated_rate: float | None = None  # 0..1（打ち切りターンの割合）
    n_propose_trade: int  # propose_trade を呼んだターン数
    disciplined_rate: float | None = None  # 起票ターンのうち 4 属性全備の割合（NULL 無視・ADR-084）


class TurnsResponse(BaseModel):
    """GET /advisor/turns のレスポンス（面別サマリ＋直近の軌跡・ADR-092）。"""

    summary: list[TurnsSummaryRow]
    recent: list[TurnItem]


def _turn_row_to_item(row: dict[str, Any]) -> TurnItem:
    """advisor_turns の生 dict を TurnItem に整形（tool_sequence の JSON を型へ・§ADR-092）。

    表示専用フラグ（called_submit_journal/notable）は tool_sequence を走査して read-time で導出する
    （列にしない＝JournalEntry の JSON 復元と同型の薄い読み変換）。
    """
    seq_raw = _as_json_list(row.get("tool_sequence"))
    dicts = [r for r in seq_raw if isinstance(r, dict)]
    names = {r.get("name") for r in dicts}
    tool_calls = [
        TurnToolCall(
            name=str(r.get("name") or ""),
            args=r["args"] if isinstance(r.get("args"), dict) else {},
        )
        for r in dicts
    ]
    disciplined = row.get("propose_trade_disciplined")
    return TurnItem(
        id=int(row["id"]),
        created_at=row.get("created_at"),
        source=row["source"],
        model=row.get("model"),
        tool_sequence=tool_calls,
        n_rounds=int(row.get("n_rounds") or 0),
        truncated=bool(row.get("truncated")),
        called_propose_trade=bool(row.get("called_propose_trade")),
        propose_trade_disciplined=None if disciplined is None else bool(disciplined),
        called_submit_journal="submit_journal" in names,
        called_submit_notable="submit_notable_stocks" in names,
    )


@router.get("/advisor/turns", response_model=TurnsResponse)
def get_advisor_turns(
    source: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    conn: Connection = Depends(get_conn),
) -> TurnsResponse:
    """AI Advisor の判断軌跡を面別サマリ＋直近の軌跡で返す（ADR-092・観測層）。

    summary は全期間の面別集計（aggregate_turns）、recent は created_at 降順の直近軌跡
    （source 指定で絞り込み）。n_propose_trade は Float 化された集約値なので int に丸める。
    """
    summary = [
        TurnsSummaryRow(
            source=r["source"],
            n_turns=int(r["n_turns"]),
            avg_rounds=r.get("avg_rounds"),
            truncated_rate=r.get("truncated_rate"),
            n_propose_trade=int(r.get("n_propose_trade") or 0),
            disciplined_rate=r.get("disciplined_rate"),
        )
        for r in repo.aggregate_turns(conn)
    ]
    recent = [
        _turn_row_to_item(r) for r in repo.list_recent_turns(conn, source=source, limit=limit)
    ]
    return TurnsResponse(summary=summary, recent=recent)
