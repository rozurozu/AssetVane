"""知識カードのプロンプト注入・意味検索（ADR-062・ADR-045 同型）。

設計の真実: docs/decisions.md ADR-062（知識カード基盤）・ADR-045（意味検索 段階A）。

旧・手法カード（method_cards.py の起動時 1 度ロード・全カード常時注入）を置き換える。カードは UI で
随時増減するため、起動時固定ではなく呼び出しのたびに DB から読む（軸1/軸2 の各ターン）。

注入方針（フェーズ2・ADR-062）:
- **ambient**（always_inject / level=market・general / level なし）は常時注入＝広く効く文脈で
  embedding 不要。機能オフでも出る（graceful）。
- **retrieval**（level=stock・sector）はチャット（query あり）で when_to_apply の意味検索を足す。
  夜AI（query None）は ambient のみ＋AI が search_cards Tool で深掘りする。
- **機能オフ fallback**: embedding 未設定なら retrieval できないので全 active を注入する（フェーズ1
  挙動・安全側＝stock/sector カードを黙って隠さない）。
"""

from __future__ import annotations

import logging
from typing import Any

from app.adapters.embedding import embed_texts, embedding_enabled
from app.db import repo
from app.db.engine import get_engine

logger = logging.getLogger(__name__)

# 常時注入する level（広く効く文脈）。stock/sector は retrieval/Tool 側で出す。
_AMBIENT_LEVELS = ("market", "general")
# チャット注入時に意味検索で足す上限（プロンプト肥大を避ける・ADR-062）。
_INJECT_RETRIEVE_LIMIT = 5


def _format_card(row: dict[str, Any]) -> str:
    """1 カードを注入用テキストへ（タイトル見出し＋本文）。"""
    title = str(row.get("title") or "").strip()
    body = str(row.get("body") or "").strip()
    return f"### {title}\n{body}" if title else body


def load_active_card_texts() -> list[str]:
    """active カードを全て整形済みテキストで返す（embedding 機能オフ時の fallback）。"""
    with get_engine().connect() as conn:
        rows = repo.list_active_knowledge_cards(conn)
    return [_format_card(r) for r in rows]


async def retrieve_cards(
    query: str,
    *,
    level: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """query に意味で近い active カードを返す（ADR-062・search_news_corpus 同型）。

    機能オフ/空 query/埋め込み失敗/vec_distance_cosine 失敗は空 list に倒す（無人運用・チャットを
    落とさない＝ADR-018）。返すのは search_knowledge_cards の行（distance 付き）。
    """
    if not query or not embedding_enabled():
        return []
    try:
        vectors = await embed_texts([query])
    except Exception:  # noqa: BLE001 — 埋め込み API 失敗を空に翻訳（ADR-018）
        logger.warning("retrieve_cards: クエリの埋め込みに失敗（ADR-062/045）")
        return []
    if not vectors:
        return []
    blob = repo.pack_embedding(vectors[0])
    try:
        with get_engine().connect() as conn:
            return repo.search_knowledge_cards(conn, blob, level=level, limit=limit)
    except Exception:  # noqa: BLE001 — sqlite-vec 未ロード等を空に翻訳（ADR-018/045）
        logger.warning("retrieve_cards: 意味検索 SQL に失敗（sqlite-vec 未ロード等・ADR-045）")
        return []


def _card_item(c: dict[str, Any]) -> dict[str, Any]:
    """カードを Tool 返却用の要約 dict に射影（embedding/timestamp は出さない）。"""
    return {
        "title": c.get("title"),
        "body": c.get("body"),
        "when_to_apply": c.get("when_to_apply"),
        "level": c.get("level"),
        "linked_signal_type": c.get("linked_signal_type"),
        "distance": c.get("distance"),
    }


async def search_cards_for_tool(
    query: str, *, level: str | None = None, limit: int = 5
) -> dict[str, Any]:
    """search_cards Tool 用: 意味検索結果を {"items":[...]} で返す（ADR-062）。

    機能オフは reason 付き空で返す（落とさない・ADR-018）。本 handler 橋渡しの実体（ADR-010/014）。
    """
    if not embedding_enabled():
        return {"items": [], "reason": "embedding 未設定（機能オフ）"}
    cards = await retrieve_cards(query, level=level, limit=limit)
    return {"items": [_card_item(c) for c in cards]}


async def load_card_texts_for_injection(query: str | None) -> list[str]:
    """注入する知識カードのテキストを返す（ADR-062 フェーズ2）。

    embedding 機能オフなら全 active を注入（フェーズ1 fallback）。オンなら ambient（always_inject /
    market・general / level なし）を常時入れ、チャット（query あり）は意味検索を足す。重複は id で
    畳む（ambient 優先・retrieval は setdefault で後置）。
    """
    if not embedding_enabled():
        return load_active_card_texts()

    with get_engine().connect() as conn:
        active = repo.list_active_knowledge_cards(conn)
    cards: dict[int, dict[str, Any]] = {}
    for c in active:
        level = c.get("level")
        if c.get("always_inject") or level in _AMBIENT_LEVELS or level is None:
            cards[int(c["id"])] = c

    if query:
        for c in await retrieve_cards(query, limit=_INJECT_RETRIEVE_LIMIT):
            cards.setdefault(int(c["id"]), c)

    return [_format_card(c) for c in cards.values()]
