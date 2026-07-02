"""判断ログ横断想起（judgment_fts）の検索クエリ（ADR-078・D-1）。

設計の真実: docs/decisions.md ADR-078・tasks/hermes-transfer-2026-07-02.md（★2 D-1）。

FTS5（trigram）で永続済みの判断ログ 3 ソース（advisor_journal / proposals / notable_picks）を
横断想起する。`search_judgments` は MATCH+bm25 でヒットを取り、origin 別に基底表へ join し直して
bookend（目的→ヒット→帰結）を組む。buy/sell 提案・注目選別のヒットには proposal_outcomes を
合流させ、その後の 20/60 営業日の実現/超過リターンを添える（get_track_record は全体集計・こちらは
個別の想起＝ADR-078）。

FTS 仮想表は schema.py の Table に無いため MATCH 部分だけ生 SQL（text）で、検索語は必ず bind する
（列名・WHERE 骨組みは定数）。bookend の join は Core select（schema の Table）で書く。戻り値は素の
dict（backend-repo-pattern）で、JSON-safe 整形（float 丸め・hit の bool 化・text 截断）は service。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import Connection, and_, select, text

from app.db.fts import FTS_TABLE
from app.db.schema import advisor_journal, notable_picks, proposal_outcomes, proposals, stocks

# snippet(): body 列（col 0）の該当箇所を《…》で囲み前後 12 トークンで抜き出す（trigram 対応）。
_SNIPPET = f"snippet({FTS_TABLE}, 0, '《', '》', '…', 12)"


def _quote_match(query: str) -> str:
    """FTS5 のクエリ構文（AND/OR/NEAR/記号）を無効化するため二重引用符でフレーズ化する。

    trigram では引用フレーズ＝部分一致（全 trigram が連続して出現）になる。クエリ中の `"` は
    二重化してエスケープする（構文注入を避け、任意テキストを 1 つのフレーズとして扱う）。
    """
    return '"' + query.replace('"', '""') + '"'


def search_judgment_fts(
    conn: Connection,
    *,
    query: str,
    code: str | None = None,
    origin: str | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """judgment_fts を MATCH+bm25 で引き、origin/ref_id/entry_date/snippet/rank を関連順で返す。

    query は呼び出し側（service）で strip 済み・3 文字以上を保証する前提（trigram は 3 文字未満を
    索引/検索できない）。origin/code は UNINDEXED 列への任意 exact 絞り。bm25 は小さいほど関連が
    高いので昇順（ORDER BY rank）で最関連が先頭。
    """
    conds = [f"{FTS_TABLE} MATCH :q"]
    params: dict[str, Any] = {"q": _quote_match(query), "lim": limit}
    if origin is not None:
        conds.append("origin = :origin")
        params["origin"] = origin
    if code is not None:
        conds.append("code = :code")
        params["code"] = code
    where = " AND ".join(conds)
    stmt = text(  # noqa: S608 — 列名・WHERE 骨組みは定数、検索語/フィルタ値は bind
        f"SELECT origin, ref_id, entry_date, {_SNIPPET} AS snippet, bm25({FTS_TABLE}) AS rank "
        f"FROM {FTS_TABLE} WHERE {where} ORDER BY rank LIMIT :lim"
    )
    return [dict(r) for r in conn.execute(stmt, params).mappings().all()]


def _outcomes_for(conn: Connection, origin_kind: str, origin_id: int) -> list[dict[str, Any]]:
    """proposal_outcomes を (origin_kind, origin_id) で引く（horizon 昇順・bookend の帰結）。"""
    stmt = (
        select(
            proposal_outcomes.c.horizon,
            proposal_outcomes.c.status,
            proposal_outcomes.c.realized_return,
            proposal_outcomes.c.excess_return,
            proposal_outcomes.c.benchmark_symbol,
            proposal_outcomes.c.hit,
            proposal_outcomes.c.as_of_date,
        )
        .where(
            and_(
                proposal_outcomes.c.origin_kind == origin_kind,
                proposal_outcomes.c.origin_id == origin_id,
            )
        )
        .order_by(proposal_outcomes.c.horizon)
    )
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


def _journal_detail(conn: Connection, ref_id: int) -> dict[str, Any]:
    """journal ヒットの bookend（所見＋当日提案・帰結なし＝テキストのみ）。"""
    row = (
        conn.execute(
            select(
                advisor_journal.c.source,
                advisor_journal.c.observations,
                advisor_journal.c.proposal,
            ).where(advisor_journal.c.id == ref_id)
        )
        .mappings()
        .first()
    )
    if row is None:
        return {}
    parts = [p for p in (row["observations"], row["proposal"]) if p]
    return {
        "source": row["source"],
        "text": "\n".join(parts),
        "kind": None,
        "code": None,
        "company_name": None,
        "status": None,
    }


def _proposal_detail(conn: Connection, ref_id: int) -> dict[str, Any]:
    """proposal ヒットの bookend（kind/銘柄/根拠/status・source は生成元 journal から導出）。"""
    row = (
        conn.execute(
            select(
                proposals.c.kind,
                proposals.c.body,
                proposals.c.rationale,
                proposals.c.status,
                advisor_journal.c.source,
            )
            .select_from(
                proposals.outerjoin(advisor_journal, proposals.c.journal_id == advisor_journal.c.id)
            )
            .where(proposals.c.id == ref_id)
        )
        .mappings()
        .first()
    )
    if row is None:
        return {}
    body_raw = row["body"]
    try:
        body = json.loads(body_raw) if body_raw else {}
    except (ValueError, TypeError):
        body = {}
    return {
        "source": row["source"] or "chat",  # journal 由来 NULL は chat（ADR-077 同型）
        "text": row["rationale"],
        "kind": row["kind"],
        "code": body.get("code"),
        "company_name": body.get("company_name"),
        "status": row["status"],
    }


def _notable_detail(conn: Connection, ref_id: int) -> dict[str, Any]:
    """notable ヒットの bookend（選定理由・社名は stocks から補完）。"""
    row = (
        conn.execute(
            select(
                notable_picks.c.reason,
                notable_picks.c.code,
                notable_picks.c.source,
                stocks.c.company_name,
            )
            .select_from(notable_picks.outerjoin(stocks, notable_picks.c.code == stocks.c.code))
            .where(notable_picks.c.id == ref_id)
        )
        .mappings()
        .first()
    )
    if row is None:
        return {}
    return {
        "source": row["source"] or "nightly",
        "text": row["reason"],
        "kind": "notable",
        "code": row["code"],
        "company_name": row["company_name"],
        "status": None,
    }


def search_judgments(
    conn: Connection,
    *,
    query: str,
    code: str | None = None,
    origin: str | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """FTS ヒットに origin 別 bookend を組んで返す（ADR-078）。

    journal はテキストのみ。proposal/notable は proposal_outcomes を合流し、horizon 別の
    実現/超過リターン＋hit（採点済み）or pending を `outcomes` に添える（丸め/截断は service）。
    """
    results: list[dict[str, Any]] = []
    for hit in search_judgment_fts(conn, query=query, code=code, origin=origin, limit=limit):
        origin_v = hit["origin"]
        ref_id = int(hit["ref_id"])
        base: dict[str, Any] = {
            "origin": origin_v,
            "ref_id": ref_id,
            "entry_date": hit.get("entry_date"),
            "snippet": hit.get("snippet"),
        }
        if origin_v == "journal":
            base.update(_journal_detail(conn, ref_id))
        elif origin_v == "proposal":
            base.update(_proposal_detail(conn, ref_id))
            base["outcomes"] = _outcomes_for(conn, "proposal", ref_id)
        elif origin_v == "notable":
            base.update(_notable_detail(conn, ref_id))
            base["outcomes"] = _outcomes_for(conn, "notable", ref_id)
        results.append(base)
    return results
