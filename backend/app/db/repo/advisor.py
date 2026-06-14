"""AI Advisor 状態 policy/journal/proposals/llm_usage（Phase 3・ADR-011/013/018/028/029）。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.schema import (
    advisor_journal,
    llm_usage,
    policy,
    proposals,
)

# ===== Phase 3: AI Advisor 状態（phase3-spec.md §8.3・ADR-011/013/018/028/029） =====
#
# [書き込みのトランザクション規律] 以下の write 関数は引数の `conn` 上で execute するだけで、
# commit はしない。呼び出し側（service.py / ルータ）が `with get_engine().begin() as conn:` で
# 包むこと（複数 write を 1 トランザクションで原子化するため＝policy 更新＋journal snapshot 等）。
# read 関数は get_conn()（engine.connect）でも begin() でも動く。


def get_policy(conn: Connection) -> dict[str, Any] | None:
    """policy の 1 行を素の dict で返す（無ければ None・spec §8.3）。

    JSON 列の型変換は services/policy.py が単一点で担う（読み=normalize_policy_row・
    書き=encode_policy_field・ADR-013）。既定値のマージも services/policy.py
    （DEFAULT_POLICY）の責務（本関数は生の行のみを返す）。
    """
    row = conn.execute(select(policy).order_by(policy.c.id).limit(1)).mappings().first()
    return dict(row) if row else None


def upsert_policy(conn: Connection, fields: dict[str, Any]) -> None:
    """policy を 1 行運用で upsert する（id 固定・ADR-013・spec §8.3）。

    fields は変更したい列のみ（部分更新可）。id は常に 1 に固定する。
    `updated_at` は呼び出し側で詰めても良いが、未指定なら UTC now を入れる。
    """
    payload = {k: v for k, v in fields.items() if k != "id"}
    payload.setdefault("updated_at", datetime.now(UTC).isoformat())
    stmt = sqlite_insert(policy).values(id=1, **payload)
    update_cols = {col: stmt.excluded[col] for col in payload}
    stmt = stmt.on_conflict_do_update(index_elements=["id"], set_=update_cols)
    conn.execute(stmt)


def insert_journal(conn: Connection, **fields: Any) -> int:
    """advisor_journal に 1 行挿入し、発行された id を返す（spec §8.3・ADR-029）。

    fields: date / source / situation_briefing / observations / proposal /
    proposed_policy_change / policy_snapshot / llm_model / created_at。
    JSON 列（situation_briefing 等）は呼び出し側で json.dumps 済みの文字列を渡す。
    """
    fields.setdefault("created_at", datetime.now(UTC).isoformat())
    fields.setdefault("source", "nightly")
    result = conn.execute(advisor_journal.insert().values(**fields))
    return int(result.lastrowid)


def get_journal(conn: Connection, journal_id: int) -> dict[str, Any] | None:
    """advisor_journal の 1 行を返す（situation_briefing 込み・GET /journal/{id}・spec §8.2）。"""
    row = (
        conn.execute(select(advisor_journal).where(advisor_journal.c.id == journal_id))
        .mappings()
        .first()
    )
    return dict(row) if row else None


def list_journal(
    conn: Connection, from_: str | None = None, to: str | None = None
) -> list[dict[str, Any]]:
    """advisor_journal を date 降順で返す（spec §8.2）。

    重い situation_briefing は一覧では返さない（必要なら get_journal で別途取得）。
    """
    cols = [
        advisor_journal.c.id,
        advisor_journal.c.date,
        advisor_journal.c.source,
        advisor_journal.c.observations,
        advisor_journal.c.proposal,
        advisor_journal.c.proposed_policy_change,
        advisor_journal.c.policy_snapshot,
        advisor_journal.c.llm_model,
        advisor_journal.c.created_at,
    ]
    stmt = select(*cols)
    if from_:
        stmt = stmt.where(advisor_journal.c.date >= from_)
    if to:
        stmt = stmt.where(advisor_journal.c.date <= to)
    stmt = stmt.order_by(advisor_journal.c.date.desc(), advisor_journal.c.id.desc())
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


def get_recent_journal_summary(conn: Connection, n: int = 1) -> str | None:
    """直近 n 件の journal observations を連結した要約文を返す（文脈・連続性・spec §8.3）。

    プロンプトの「直近の投資日記」層に差すための軽い文字列。無ければ None。
    """
    stmt = (
        select(advisor_journal.c.date, advisor_journal.c.observations)
        .order_by(advisor_journal.c.date.desc(), advisor_journal.c.id.desc())
        .limit(n)
    )
    rows = conn.execute(stmt).mappings().all()
    if not rows:
        return None
    parts = [f"{r['date']}: {r['observations']}" for r in rows if r["observations"]]
    return "\n".join(parts) if parts else None


def insert_proposal(conn: Connection, **fields: Any) -> int:
    """proposals に 1 行挿入し id を返す（spec §8.3・ADR-001/019）。

    fields: created_date / kind / body / rationale / status / outcome /
    journal_id / depends_on。body は呼び出し側で json.dumps 済みの文字列。
    """
    fields.setdefault("status", "pending")
    result = conn.execute(proposals.insert().values(**fields))
    return int(result.lastrowid)


def list_proposals(conn: Connection, status: str | None = None) -> list[dict[str, Any]]:
    """proposals を created_date 降順で返す（status 指定で絞り込み・spec §8.2）。"""
    stmt = select(proposals)
    if status:
        stmt = stmt.where(proposals.c.status == status)
    stmt = stmt.order_by(proposals.c.created_date.desc(), proposals.c.id.desc())
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


def pending_trade_proposal_exists(conn: Connection, kind: str, code: str) -> bool:
    """同一 (kind, code) の pending な売買提案が既にあるか（ADR-052・重複起票防止）。

    proposals は code 専用列を持たず body(JSON) に詰めるため、pending かつ同 kind の行を
    引いて Python 側で body の code を突き合わせる（migration 不要）。reject/approve 済みは
    対象外＝状況変化後の再提案は通す（毎晩の pending 氾濫だけを抑える）。
    """
    stmt = select(proposals.c.body).where(proposals.c.status == "pending", proposals.c.kind == kind)
    for (body,) in conn.execute(stmt).all():
        if not body:
            continue
        try:
            if json.loads(body).get("code") == code:
                return True
        except (ValueError, TypeError):
            continue
    return False


def get_proposal(conn: Connection, proposal_id: int) -> dict[str, Any] | None:
    """proposals の 1 行を返す（無ければ None・spec §8.3）。"""
    row = conn.execute(select(proposals).where(proposals.c.id == proposal_id)).mappings().first()
    return dict(row) if row else None


def update_proposal_status(
    conn: Connection,
    proposal_id: int,
    status: str,
    outcome: str | None = None,
    resolved_at: str | None = None,
) -> None:
    """proposals.status を遷移する（approved/rejected・spec §8.3）。

    resolved_at 未指定なら UTC now を入れる。outcome は任意。
    """
    values: dict[str, Any] = {
        "status": status,
        "resolved_at": resolved_at or datetime.now(UTC).isoformat(),
    }
    if outcome is not None:
        values["outcome"] = outcome
    conn.execute(proposals.update().where(proposals.c.id == proposal_id).values(**values))


# --- llm_usage（LLM コストガードレール台帳・ADR-028・spec §7.1） ---


def insert_llm_usage(conn: Connection, **fields: Any) -> int:
    """llm_usage に 1 行（per-call）積む（ADR-028・spec §7.1）。

    fields: created_at / source / model / tokens_in / tokens_out / cost_usd。
    cost_usd は OpenRouter の usage.cost。Ollama は 0。
    """
    fields.setdefault("created_at", datetime.now(UTC).isoformat())
    fields.setdefault("cost_usd", 0.0)
    result = conn.execute(llm_usage.insert().values(**fields))
    return int(result.lastrowid)


def sum_llm_cost_month(conn: Connection, year_month: str) -> float:
    """指定年月（'YYYY-MM'）の cost_usd 合計を返す（当月ガード判定・spec §7.1）。

    created_at（ISO8601）の先頭 7 文字でマッチする。行が無ければ 0.0。
    """
    stmt = select(func.coalesce(func.sum(llm_usage.c.cost_usd), 0.0)).where(
        llm_usage.c.created_at.like(f"{year_month}%")
    )
    return float(conn.execute(stmt).scalar() or 0.0)
