"""夜間バッチ: 知識カードの埋め込み生成ジョブ（ADR-062・ADR-045 段階A 同型）。

設計の真実: docs/decisions.md ADR-062（知識カード基盤）・ADR-045（意味検索）・batch-pattern。

knowledge_cards の `when_to_apply`（適用条件＝retrieval キー）を埋め込み、フェーズ2 の意味検索
（カードの自動 retrieve）に乗せる。embedding が NULL または embed_model 不一致の行をまとめて埋める。
保存時に best-effort で即時埋め込みもする（embed_card_best_effort）が、失敗/機能オフ時の取りこぼしを
この夜間ジョブが拾う（news の即時＋夜間の二段と同じ・ADR-045）。

格納は float32 LE の BLOB（vec_distance_cosine が読む）。embed_texts は OpenAI 互換アダプタ越し
（ADR-010/012）で、同期ジョブから asyncio.run で駆動する（embed_news の流儀）。

機能オフ耐性（ADR-006/018）: embedding 未設定なら静かに skip（ok=True・rows=0）。機能が有効なのに
API 失敗が出た夜は ok=False で runner の Discord 通知に乗せる（embed_news と契約対称・C-7）。
"""

from __future__ import annotations

import asyncio
import logging

from app.adapters.embedding import embed_texts, embedding_enabled, embedding_model
from app.batch import state
from app.batch.runner import JobResult
from app.db import repo
from app.db.engine import get_engine
from app.services.knowledge_cards import build_card_retrieval_text

logger = logging.getLogger(__name__)

# 1 バッチで埋め込み API へまとめて投げる件数（embed_news と同値）。
EMBED_BATCH = 100


def run() -> JobResult:
    """when_to_apply が NULL/モデル不一致の knowledge_cards を埋め込む（ADR-062・embed_news 同型）。

    embedding 未設定なら ok=True・rows=0 で静かに skip。設定時は list_cards_needing_embedding を
    EMBED_BATCH 件ずつ取り、when_to_apply を embed_texts でまとめて埋め込み → pack_embedding →
    update_card_embedding（1 begin に束ねる W2）。1 バッチ失敗は握り打ち切るが failed_batches>0 なら
    ok=False（embed_news と対称・ADR-018・C-7）。ジョブ境界の例外も握り ok=False で返す。
    """
    if not embedding_enabled():
        return JobResult(name="embed_cards", ok=True, rows=0, detail="embedding 未設定で skip")

    model = embedding_model()
    embedded = 0
    failed_batches = 0
    try:
        while True:
            # 埋め込み API を大量バッチで叩き長引くことがある。バッチ境界で should_stop を見て
            # 中断する（stop_aware・ADR-036 追補/070）。埋めた分は冪等 UPSERT 済み。
            if state.should_stop():
                break
            with get_engine().connect() as conn:
                rows = repo.list_cards_needing_embedding(
                    conn, current_model=model, limit=EMBED_BATCH
                )
            if not rows:
                break

            try:
                texts = [build_card_retrieval_text(r) for r in rows]
                vectors = asyncio.run(embed_texts(texts))
            except Exception:  # noqa: BLE001 — 1 バッチの埋め込み失敗は握り打ち切り（翌晩再試行・ADR-018）
                logger.warning("embed_cards: 1 バッチの埋め込みに失敗（継続する・ADR-062）")
                failed_batches += 1
                break

            if not vectors:
                break  # 機能オフ相当（None）/空。残りは次回に回す
            with get_engine().begin() as conn:
                for row, vec in zip(rows, vectors, strict=True):
                    repo.update_card_embedding(
                        conn, int(row["id"]), repo.pack_embedding(vec), model
                    )
                    embedded += 1

            if len(rows) < EMBED_BATCH:
                break  # 取り切った
    except Exception as exc:  # noqa: BLE001 — ジョブ境界で握り runner に返す
        logger.exception("embed_cards: 失敗")
        return JobResult(name="embed_cards", ok=False, rows=embedded, detail=str(exc))

    detail = f"知識カード埋め込み {embedded} 件"
    if failed_batches:
        detail += f"（{failed_batches} バッチ失敗・翌晩に再試行）"
    return JobResult(name="embed_cards", ok=failed_batches == 0, rows=embedded, detail=detail)


def embed_card_best_effort(card_id: int) -> None:
    """カード保存直後の即時埋め込み（best-effort・失敗は握る・ADR-062 追補）。

    embedding 機能オフ・本文ベース合成テキストが空なら何もしない。失敗しても呼び出し側（router）の
    保存自体は成功済みなので握りつぶす（取りこぼしは夜間 run() が拾う）。await は DB 書き込み tx の
    外で駆動する（C-6 の規律）。
    """
    if not embedding_enabled():
        return
    with get_engine().connect() as conn:
        card = repo.get_knowledge_card(conn, card_id)
    if card is None:
        return
    source_text = build_card_retrieval_text(card)
    if not source_text:
        return
    model = embedding_model()
    try:
        vectors = asyncio.run(embed_texts([source_text]))
        if not vectors:
            return
        with get_engine().begin() as conn:
            repo.update_card_embedding(conn, card_id, repo.pack_embedding(vectors[0]), model)
    except Exception:  # noqa: BLE001 — 即時埋め込みは best-effort。失敗は夜間ジョブが拾う（ADR-045）
        logger.warning(
            "embed_card_best_effort: カード %s の即時埋め込みに失敗（夜間で拾う）", card_id
        )
