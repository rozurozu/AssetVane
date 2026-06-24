"""夜間バッチ: ニュース埋め込み生成ジョブ（ADR-045 段階A）。

設計の真実: docs/decisions.md ADR-045（ニュース意味検索）・batch-pattern。

NIGHTLY_JOBS の investigate_dossier.run の**後**・notify_cost_warn.run の**前**に置く。
全ニュース書込（fetch_general_news / fetch_sector_news / run_advisor / investigate_dossier 等が
news に summary を入れ終わった後）に、embedding が NULL または embed_model 不一致の行をまとめて
埋め込む（要約済み台帳を意味検索に乗せる）。

格納は float32 LE の BLOB（vec_distance_cosine が読む）。embed_texts は OpenAI 互換アダプタ越し
（ADR-010/012）で、同期ジョブから asyncio.run で駆動する（fetch_general_news.run の流儀）。

機能オフ耐性（ADR-006/018）: embedding 未設定なら静かに skip（ok=True・rows=0）。
失敗の扱い（tasks/review-2026-06-12.md C-7）: 機能が**有効なのに** API 呼び出しが失敗した
場合は ok=False で返し runner の Discord 通知に乗せる（tag 系ジョブと契約対称・「黙って
失敗を握りつぶさない」＝ADR-018。ok=True のままだと embedding API 停止で意味検索が静かに
陳腐化する）。部分的に成功した埋め込みは冪等 UPSERT で永続済みのまま残し、翌晩は未埋め込み
分だけが再試行される（自己回復性は維持）。ジョブ境界の例外も握り ok=False で返す
（後続ジョブを止めない・ADR-018）。冪等性（ADR-002）: 既に当該モデルで埋め込み済みの行は
list_news_needing_embedding が返さないため、再実行しても二重埋め込みしない。
"""

from __future__ import annotations

import asyncio
import logging

from app.adapters.embedding import embed_texts, embedding_enabled, embedding_model
from app.batch.runner import JobResult
from app.db import repo
from app.db.engine import get_engine

logger = logging.getLogger(__name__)

# 1 バッチで OpenAI 互換 embeddings API へまとめて投げる件数（API 1 回あたりの上限を緩く保つ）。
EMBED_BATCH = 100


def run() -> JobResult:
    """embedding が NULL/モデル不一致の news 行を埋め込み、埋めた件数を集約する（ADR-045）。

    embedding 未設定なら ok=True・rows=0 で静かに skip（ADR-006・ADR-045「未設定なら
    静かに機能オフ」）。設定時は list_news_needing_embedding を EMBED_BATCH 件ずつ取り、
    summary を embed_texts でまとめて埋め込み→ pack_embedding で BLOB 化→
    update_news_embedding（同一トランザクション）。1 バッチの失敗は握って打ち切るが、
    failed_batches > 0 なら ok=False で返し runner の通知に乗せる（tag 系と契約対称・
    ADR-018・tasks/review-2026-06-12.md C-7。成功済み埋め込みは永続済みのまま残し翌晩に
    未埋め込み分だけ再試行＝自己回復性は維持）。ジョブ境界の例外も握り ok=False で返す。
    """
    if not embedding_enabled():
        return JobResult(name="embed_news", ok=True, rows=0, detail="embedding 未設定で skip")

    model = embedding_model()
    embedded = 0
    failed_batches = 0
    try:
        while True:
            with get_engine().connect() as conn:
                rows = repo.list_news_needing_embedding(
                    conn, current_model=model, limit=EMBED_BATCH
                )
            if not rows:
                break

            try:
                vectors = asyncio.run(embed_texts([r["summary"] for r in rows]))
            except Exception:  # noqa: BLE001 — 1 バッチの埋め込み失敗は握り次バッチへ（ADR-018）
                logger.warning("embed_news: 1 バッチの埋め込みに失敗（継続する・ADR-045）")
                failed_batches += 1
                break  # 同じ行を再取得して無限ループしないよう打ち切る（残りは翌晩に拾う）

            if not vectors:
                break  # 機能オフ相当（None）/空。残りは次回に回す
            with get_engine().begin() as conn:
                for row, vec in zip(rows, vectors, strict=True):
                    repo.update_news_embedding(
                        conn, int(row["id"]), repo.pack_embedding(vec), model
                    )
                    embedded += 1

            if len(rows) < EMBED_BATCH:
                break  # 取り切った
    except Exception as exc:  # noqa: BLE001 — ジョブ境界で握り runner に返す
        logger.exception("embed_news: 失敗")
        return JobResult(name="embed_news", ok=False, rows=embedded, detail=str(exc))

    detail = f"ニュース埋め込み {embedded} 件"
    if failed_batches:
        detail += f"（{failed_batches} バッチ失敗・翌晩に再試行）"
    # 機能が有効なのに API 呼び出しが失敗した夜は ok=False（tag 系と契約対称・ADR-018・C-7）。
    return JobResult(name="embed_news", ok=failed_batches == 0, rows=embedded, detail=detail)
