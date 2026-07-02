"""AI 過去提案の市場結果採点（proposal_outcomes）のクエリ（ADR-077・テーマ A）。

設計の真実: docs/decisions.md ADR-077・tasks/hermes-transfer-2026-07-02.md。

夜バッチ初の backward-looking ジョブ score_proposal_outcomes（services/track_record.py）が使う
「採点対象の抽出」「採点結果の冪等 UPSERT」「Tool 用の集計/直近取得」を持つ。戻り値は素の dict
（backend-repo-pattern）。集約（AVG）は type_coerce(Float()) で Float 化し Decimal を LLM/MCP 境界に
流さない（advisor-tool-pattern「返り値は JSON-safe」の repo 側の担保）。書き込みは接続注入で commit
しない（W2＝呼び出し側 job が begin() 境界を所有）。UPSERT で冪等（ADR-002）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, Float, and_, func, select, type_coerce
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.schema import (
    advisor_journal,
    notable_picks,
    proposal_outcomes,
    proposals,
    stocks,
    us_stocks,
)

# --- 採点対象の抽出 ---


def list_scorable_trade_proposals(conn: Connection) -> list[dict[str, Any]]:
    """採点対象の buy/sell 提案を返す（ADR-077）。source は生成元 journal から導出。

    proposals を advisor_journal に LEFT JOIN し kind IN ('buy','sell') のみ返す。source は
    journal.source（NULL のときは呼び出し側 service が 'chat' に倒す＝chat の propose_trade 単独か
    nightly 縮退）。body は生 TEXT のまま返す（json.loads で code/market を取るのは service）。
    policy_change/rebalance は対象外（銘柄の方向性が無い＝ADR-077 決定②）。
    """
    stmt = (
        select(
            proposals.c.id,
            proposals.c.created_date,
            proposals.c.kind,
            proposals.c.body,
            advisor_journal.c.source,
        )
        .select_from(
            proposals.outerjoin(advisor_journal, proposals.c.journal_id == advisor_journal.c.id)
        )
        .where(proposals.c.kind.in_(["buy", "sell"]))
        .order_by(proposals.c.id)
    )
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


def list_scorable_notable_picks(conn: Connection) -> list[dict[str, Any]]:
    """採点対象の notable_picks を返す（ADR-077・JP・非方向＝hit なし）。

    notable_picks は JP ユニバース限定（ADR-067）。source 列を直接持つ。code/date/source を返し、
    service が JP 価格＋^TPX ベンチで実現/超過リターンだけ記録する（的中判定はしない＝ADR-077）。
    """
    stmt = select(
        notable_picks.c.id,
        notable_picks.c.date,
        notable_picks.c.code,
        notable_picks.c.source,
    ).order_by(notable_picks.c.id)
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


# --- 採点結果の冪等 UPSERT（W2） ---


def upsert_proposal_outcome(conn: Connection, **fields: Any) -> None:
    """採点 1 行を proposal_outcomes に UPSERT する（ADR-077・冪等）。

    衝突キー UNIQUE(origin_kind,origin_id,horizon)。pending→final の上書き・再実行に耐える
    （ADR-002）。fields は schema の列名（origin_kind/origin_id/source/kind/code/market/entry_date/
    horizon と、採点で埋まる entry_priced_date 以降）。接続注入で commit しない（W2＝呼び出し側
    job が begin() 境界を所有）。scored_at 未指定なら UTC now を入れる。
    """
    fields.setdefault("scored_at", datetime.now(UTC).isoformat())
    stmt = sqlite_insert(proposal_outcomes).values(**fields)
    update_cols = {
        c: stmt.excluded[c] for c in fields if c not in ("origin_kind", "origin_id", "horizon")
    }
    stmt = stmt.on_conflict_do_update(
        index_elements=["origin_kind", "origin_id", "horizon"], set_=update_cols
    )
    conn.execute(stmt)


# --- Tool 用の集計・直近取得 ---


def _outcome_filters(source: str | None, kind: str | None, horizon: int | None) -> list[Any]:
    """proposal_outcomes への任意フィルタ（source/kind/horizon）を where 句のリストで返す。"""
    conds: list[Any] = [proposal_outcomes.c.status == "final"]
    if source:
        conds.append(proposal_outcomes.c.source == source)
    if kind:
        conds.append(proposal_outcomes.c.kind == kind)
    if horizon is not None:
        conds.append(proposal_outcomes.c.horizon == horizon)
    return conds


def aggregate_track_record(
    conn: Connection,
    *,
    source: str | None = None,
    kind: str | None = None,
    horizon: int | None = None,
) -> list[dict[str, Any]]:
    """final の採点を source×kind×horizon で集計する（ADR-077・Tool の成績サマリ）。

    count（final 件数）・hit_rate（AVG(hit)＝方向性提案のみ・notable は NULL）・平均実現/超過
    リターン・ベンチ欠測件数を返す。AVG/SUM は type_coerce(Float()) で Float 化（Decimal を返さ
    ない＝backend-repo-pattern）。NULL は AVG が無視（hit_rate/avg_excess は非 NULL 分の平均）。
    """
    stmt = (
        select(
            proposal_outcomes.c.source,
            proposal_outcomes.c.kind,
            proposal_outcomes.c.horizon,
            func.count().label("count"),
            type_coerce(func.avg(proposal_outcomes.c.hit), Float()).label("hit_rate"),
            type_coerce(func.avg(proposal_outcomes.c.realized_return), Float()).label(
                "avg_realized_return"
            ),
            type_coerce(func.avg(proposal_outcomes.c.excess_return), Float()).label(
                "avg_excess_return"
            ),
            type_coerce(
                func.coalesce(func.sum(proposal_outcomes.c.benchmark_fallback), 0), Float()
            ).label("n_benchmark_fallback"),
        )
        .where(and_(*_outcome_filters(source, kind, horizon)))
        .group_by(proposal_outcomes.c.source, proposal_outcomes.c.kind, proposal_outcomes.c.horizon)
        .order_by(proposal_outcomes.c.source, proposal_outcomes.c.kind, proposal_outcomes.c.horizon)
    )
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


def list_recent_final_outcomes(
    conn: Connection,
    *,
    source: str | None = None,
    kind: str | None = None,
    horizon: int | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """直近の final 採点を company_name 付きで返す（ADR-077・Tool の個別 recent）。

    company_name は JP=stocks・US=us_stocks を LEFT JOIN して coalesce で補う（JP 5 桁と US
    ティッカーは衝突しない前提）。scored_at 降順（同値 id 降順）で limit 件。
    """
    company_name = func.coalesce(stocks.c.company_name, us_stocks.c.company_name).label(
        "company_name"
    )
    stmt = (
        select(
            proposal_outcomes.c.origin_kind,
            proposal_outcomes.c.source,
            proposal_outcomes.c.kind,
            proposal_outcomes.c.code,
            company_name,
            proposal_outcomes.c.market,
            proposal_outcomes.c.entry_date,
            proposal_outcomes.c.horizon,
            proposal_outcomes.c.as_of_date,
            proposal_outcomes.c.realized_return,
            proposal_outcomes.c.excess_return,
            proposal_outcomes.c.benchmark_symbol,
            proposal_outcomes.c.hit,
        )
        .select_from(
            proposal_outcomes.outerjoin(
                stocks, proposal_outcomes.c.code == stocks.c.code
            ).outerjoin(us_stocks, proposal_outcomes.c.code == us_stocks.c.symbol)
        )
        .where(and_(*_outcome_filters(source, kind, horizon)))
        .order_by(proposal_outcomes.c.scored_at.desc(), proposal_outcomes.c.id.desc())
        .limit(limit)
    )
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


def count_pending_outcomes(conn: Connection) -> int:
    """horizon 未経過（status='pending'）の採点件数を返す（ADR-077・Tool の pending_count）。"""
    stmt = select(func.count()).where(proposal_outcomes.c.status == "pending")
    return int(conn.execute(stmt).scalar() or 0)


def latest_final_as_of(conn: Connection) -> str | None:
    """final 採点の最新到達日（as_of_date の最大）を返す（ADR-077・鮮度注記用）。無ければ None。"""
    stmt = select(func.max(proposal_outcomes.c.as_of_date)).where(
        proposal_outcomes.c.status == "final"
    )
    return conn.execute(stmt).scalar()
