"""知識カードのプロンプト注入・意味検索（ADR-062・ADR-045 同型）。

設計の真実: docs/decisions.md ADR-062（知識カード基盤・追補）・ADR-045（意味検索 段階A）。

旧・手法カード（method_cards.py の起動時 1 度ロード・全カード常時注入）を置き換える。カードは UI で
随時増減するため、呼び出しのたびに DB から読む（軸1/軸2 の各ターン）。

注入方針（ADR-062 追補・純 retrieval）:
- **always_inject** のカードだけ常時注入（「必ず見せたい」例外フラグ）。
- **チャット**（query あり）は本文ベース embedding の意味検索 top-K を足す。
- **夜AI**（query None）は always_inject のみ＋AI が `search_cards` Tool で深掘り。
- **機能オフ fallback**: embedding 未設定なら retrieval できないので全 active を注入（安全側）。

埋め込み元は title+when_to_apply+body の合成テキスト（build_card_retrieval_text）。when_to_apply が
空でも本文で引ける。ランクは distance/weight（重要度）で重み付け、注入には created_at（鮮度）を
添えて AI が古さを解釈できるようにする。
"""

from __future__ import annotations

import logging
from typing import Any

from app.adapters.embedding import embed_texts, embedding_enabled
from app.db import repo
from app.db.engine import get_engine

logger = logging.getLogger(__name__)

# チャット注入時に意味検索で足す上限（プロンプト肥大を避ける・ADR-062）。
_INJECT_RETRIEVE_LIMIT = 5

# 銘柄ノート（code 付き）の exact-match 注入上限（weight 降順・ADR-062 追補・③(1)）。
# chat は focus の 1 銘柄、夜 AI は注目候補ぶんを weight 降順で束ねる（実質は数枚だが
# プロンプト肥大の保険で切る）。
_STOCK_INJECT_LIMIT = 8


def build_card_retrieval_text(row: dict[str, Any]) -> str:
    """埋め込み元の合成テキストを組む（title＋when_to_apply＋body・空は飛ばす・ADR-062 追補）。

    embed_cards ジョブ・保存時 best-effort の両方がこれを埋め込む（検索キーと格納の一致を保つ）。
    """
    parts = [str(row.get(c) or "").strip() for c in ("title", "when_to_apply", "body")]
    return "\n".join(p for p in parts if p)


def _format_card(row: dict[str, Any]) -> str:
    """1 カードを注入用テキストへ（見出し＋追加日〔鮮度〕＋本文）。"""
    title = str(row.get("title") or "").strip()
    body = str(row.get("body") or "").strip()
    created = str(row.get("created_at") or "")[:10]  # 'YYYY-MM-DD'（鮮度ヒント）
    if not title:
        return body
    head = f"### {title}（追加 {created}）" if created else f"### {title}"
    return f"{head}\n{body}"


def _card_item(c: dict[str, Any]) -> dict[str, Any]:
    """カードを Tool 返却用の dict に射影（id は adjust_card_weight 参照に必要・ADR-062）。"""
    keys = (
        "id",
        "title",
        "body",
        "when_to_apply",
        "level",
        "market",
        "code",
        "weight",
        "created_at",
        "updated_at",
        "distance",
    )
    return {k: c.get(k) for k in keys}


def load_active_card_texts() -> list[str]:
    """active カードを全て整形済みテキストで返す（embedding 機能オフ時の fallback）。"""
    with get_engine().connect() as conn:
        rows = repo.list_active_knowledge_cards(conn)
    return [_format_card(r) for r in rows]


async def retrieve_cards(
    query: str,
    *,
    level: str | None = None,
    only_unscoped: bool = False,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """query に意味で近い active カードを weight 重み付けで返す（ADR-062・search_news 同型）。

    機能オフ/空 query/埋め込み失敗/vec_distance_cosine 失敗は空 list に倒す（無人運用・チャットを
    落とさない＝ADR-018）。only_unscoped=True は銘柄ノート（code 付き）を除外＝汎用の意味検索
    プールを非銘柄カードに絞る（ADR-062 追補・③(2)）。返すのは search_knowledge_cards の行
    （distance 付き）。
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
            return repo.search_knowledge_cards(
                conn, blob, level=level, only_unscoped=only_unscoped, limit=limit
            )
    except Exception:  # noqa: BLE001 — sqlite-vec 未ロード等を空に翻訳（ADR-018/045）
        logger.warning("retrieve_cards: 意味検索 SQL に失敗（sqlite-vec 未ロード等・ADR-045）")
        return []


async def search_cards_for_tool(
    query: str,
    *,
    level: str | None = None,
    code: str | None = None,
    market: str | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    """search_cards Tool 用: 検索結果を {"items":[...]} で返す（ADR-062・追補で code 対応）。

    code 指定時はその銘柄の active ノートを **exact-match で weight 降順全返し**（意味クエリは無視・
    embedding 有無を問わない＝③(1) 同型）。code 無しは非銘柄カードの意味検索（③(2) と対称・
    機能オフは
    reason 付き空で落とさない＝ADR-018）。本 handler 橋渡しの実体（ADR-010/014）。
    """
    if code:
        with get_engine().connect() as conn:
            cards = repo.list_active_cards_by_codes(conn, codes=[code], market=market, limit=limit)
        return {"items": [_card_item(c) for c in cards]}
    if not embedding_enabled():
        return {"items": [], "reason": "embedding 未設定（機能オフ）"}
    cards = await retrieve_cards(query, level=level, only_unscoped=True, limit=limit)
    return {"items": [_card_item(c) for c in cards]}


def _dedup_ordered_codes(*groups: list[str] | None) -> list[str]:
    """複数グループの code を順序保持で dedup（focus_code＋candidate_codes を 1 本に束ねる）。"""
    seen: set[str] = set()
    out: list[str] = []
    for group in groups:
        for raw in group or []:
            code = str(raw)
            if code and code not in seen:
                seen.add(code)
                out.append(code)
    return out


async def load_card_texts_for_injection(
    query: str | None,
    *,
    focus_code: str | None = None,
    candidate_codes: list[str] | None = None,
) -> list[str]:
    """注入する知識カードのテキストを返す（ADR-062 追補・純 retrieval＋銘柄ノート exact-match）。

    注入は `ambient(always_inject の非銘柄) ∪ 完全一致(focus/候補の銘柄ノート)`
    `∪ 意味検索top-K(非銘柄)` を id で dedup する（ADR-062 追補・③）。
    - chat は focus_code＝見ている銘柄、夜 AI は candidate_codes＝注目候補で銘柄ノートを exact-match
      注入する（意味距離を問わず・③(1)）。
    - 銘柄ノート（code 付き）は汎用の意味検索プール／ambient からは除外し、他銘柄会話への漏れを防ぐ
      （③(2)。always_inject は router が code 付きで 0 に矯正＝ambient に混ざらない）。
    - embedding 機能オフは意味検索できないので「非銘柄 active を全注入＋銘柄ノートは
      exact-match のみ」に倒す（fallback・安全側）。
    """
    codes = _dedup_ordered_codes([focus_code] if focus_code else None, candidate_codes)
    with get_engine().connect() as conn:
        active = repo.list_active_knowledge_cards(conn)
        stock_cards = (
            repo.list_active_cards_by_codes(conn, codes=codes, limit=_STOCK_INJECT_LIMIT)
            if codes
            else []
        )

    if not embedding_enabled():
        # 機能オフ: 非銘柄 active を全注入（意味検索できないため）＋銘柄ノートは exact-match のみ。
        cards: dict[int, dict[str, Any]] = {int(c["id"]): c for c in active if not c.get("code")}
    else:
        # ambient: always_inject の非銘柄カードのみ（銘柄ノートは exact-match でだけ出す）。
        cards = {int(c["id"]): c for c in active if c.get("always_inject") and not c.get("code")}

    # 完全一致: 見ている/注目候補の銘柄ノート（意味距離を問わず・③(1)）。
    for c in stock_cards:
        cards.setdefault(int(c["id"]), c)

    # 意味検索 top-K: 非銘柄カードのみ（③(2)。機能オフ/query なしは retrieve_cards が空を返す）。
    if query and embedding_enabled():
        for c in await retrieve_cards(query, only_unscoped=True, limit=_INJECT_RETRIEVE_LIMIT):
            cards.setdefault(int(c["id"]), c)

    return [_format_card(c) for c in cards.values()]
