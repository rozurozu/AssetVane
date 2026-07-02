"""判断ログ横断想起（search_judgments）のオーケストレーション（ADR-078・D-1）。

設計の真実: docs/decisions.md ADR-078・tasks/hermes-transfer-2026-07-02.md（★2 D-1）。

repo.search_judgments（FTS+bookend）の前後で、クエリ長ガード（trigram は 3 文字未満を扱えない）と
Tool 返り値の JSON-safe 整形（float 丸め・hit の bool 化・長い所見の截断）を担う。FTS 未作成等で
SQL が失敗しても握って items 空＋理由で返す（無人運用/チャットを落とさない＝ADR-018・search_news
同型）。埋め込みは使わない純 SQL 経路なので同期関数（handler が connect() を渡す）。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import Connection

from app.db import repo

logger = logging.getLogger(__name__)

# trigram は 3 文字未満のクエリで trigram を生成できず MATCH が空振りするため、下限を明示する。
_MIN_QUERY_LEN = 3
# 所見は 1 日分の長文になり得るので返却テキストは上限で截断する（snippet は別途返す）。
_TEXT_CAP = 600


def _round(value: float | None, digits: int = 4) -> float | None:
    """float を丸める（None は素通し）。"""
    return None if value is None else round(float(value), digits)


def _shape_outcomes(outcomes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """proposal_outcomes 行を JSON-safe に整形する（hit は bool・リターンは丸め）。"""
    return [
        {
            "horizon": int(o["horizon"]),
            "status": o["status"],
            "realized_return": _round(o["realized_return"]),
            "excess_return": _round(o["excess_return"]),
            "benchmark_symbol": o["benchmark_symbol"],
            "hit": None if o["hit"] is None else bool(o["hit"]),
            "as_of_date": o["as_of_date"],
        }
        for o in outcomes
    ]


def _shape(hit: dict[str, Any]) -> dict[str, Any]:
    """repo のヒット 1 件を Tool 返却 dict に整形する（JSON-safe・text 截断）。"""
    text_val = hit.get("text")
    if isinstance(text_val, str) and len(text_val) > _TEXT_CAP:
        text_val = text_val[:_TEXT_CAP] + "…"
    out: dict[str, Any] = {
        "origin": hit["origin"],
        "ref_id": hit["ref_id"],
        "entry_date": hit.get("entry_date"),
        "snippet": hit.get("snippet"),
        "text": text_val,
        "kind": hit.get("kind"),
        "code": hit.get("code"),
        "company_name": hit.get("company_name"),
        "status": hit.get("status"),
        "source": hit.get("source"),
    }
    outcomes = hit.get("outcomes")
    if outcomes is not None:  # journal は帰結なし＝キー自体を出さない
        out["outcomes"] = _shape_outcomes(outcomes)
    return out


def search_judgments_for_tool(
    conn: Connection,
    *,
    query: str,
    code: str | None = None,
    origin: str | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    """判断ログを FTS 横断想起して {"items": [...]} を返す（ADR-078）。

    query 3 文字未満は空＋理由（trigram の下限）。SQL 失敗（FTS 未作成等）も握って空＋理由。
    各 item は origin/日付/snippet/本文＋（proposal/notable は）horizon 別の帰結（outcomes）。
    """
    q = (query or "").strip()
    if len(q) < _MIN_QUERY_LEN:
        return {"items": [], "reason": f"検索語は {_MIN_QUERY_LEN} 文字以上が必要です（trigram）"}
    try:
        rows = repo.search_judgments(conn, query=q, code=code, origin=origin, limit=limit)
    except Exception:  # noqa: BLE001 — FTS 未作成/SQL 失敗を空＋理由に翻訳（ADR-018・search_news 同型）
        logger.warning(
            "search_judgments_for_tool: 判断ログ検索 SQL 失敗（judgment_fts 未作成?・ADR-078）"
        )
        return {"items": [], "reason": "判断ログ検索が利用できません"}
    return {"items": [_shape(r) for r in rows]}
