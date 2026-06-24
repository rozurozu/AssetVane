"""夜間バッチ: テーマ語彙の埋め込み生成＋near_duplicate 判定ジョブ（ADR-050 改訂・ADR-045）。

設計の真実: docs/decisions.md ADR-050 改訂（語彙 reconcile は目録層で）・ADR-045
（embedding＋vec_distance_cosine 流用）・batch-pattern。embed_news の同型ジョブ。

NIGHTLY_JOBS では embed_news.run の直後に置く（tag_us_themes が当夜増やした語彙を埋め込み、
near_duplicate_of を判定する）。

段取り（embed_news のバッチループ・打ち切り方針を踏襲）:
  1. list_themes_needing_embedding（embedding NULL or embed_model 不一致）を _EMBED_BATCH 件
     ずつ取り、テーマ名を embed_texts でまとめて埋め込み → pack_embedding →
     update_theme_embedding（W2・1 バッチ 1 begin で束ねる＝embed_news 同型）。1 バッチの
     埋め込み失敗は握って打ち切り、
     残りは翌晩に拾う（ADR-018）。
  2. **near_duplicate_of 判定も本ジョブで行う**（新規埋め込み分のみ）: 埋め込んだ各テーマに
     ついて find_nearest_theme で最近接テーマを引き、余弦距離が _NEAR_DUP_MAX_DISTANCE 以下なら
     set_theme_near_duplicate(name, nearest)、超えるなら None をセットする（再埋め込み時の
     過去フラグ解除を兼ねる。set_theme_near_duplicate は素の set/clear なので既存フラグの一括
     再判定はしない＝新規埋め込み分のみで可）。**自動マージはしない**＝near_duplicate_of は
     重複「候補」の提示フラグであり、themes/stock_themes の行を統合・削除しない（ADR-050）。
     判定失敗（sqlite-vec 未ロード等）は握って degrade（embedding は書けている・フラグ判定だけ
     skip して翌晩のモデル不一致再埋め込みには乗らないが、候補提示は best-effort で良い）。

機能オフ耐性（ADR-006/018/045）: embedding 未設定なら静かに skip（ok=True・rows=0・
embedding=NULL で degrade＝タガーのプロンプト照合だけで語彙 reconcile が回る）。
失敗の扱い（tasks/review-2026-06-12.md C-7）: 機能が**有効なのに** API 呼び出しが失敗した
場合は ok=False で返し runner の Discord 通知に乗せる（tag 系ジョブと契約対称・「黙って
失敗を握りつぶさない」＝ADR-018。ok=True のままだと embedding API 停止で語彙の埋め込みが
静かに陳腐化する）。部分的に成功した埋め込みは冪等 UPSERT で永続済みのまま残し、翌晩は
未埋め込み分だけが再試行される（自己回復性は維持）。near_dup 判定の失敗は従来どおり握って
degrade（候補提示は best-effort・ok には響かせない）。
冪等性（ADR-002）: 埋め込み済み（現行モデル一致）の行は list_themes_needing_embedding が
返さないため、再実行しても二重埋め込みしない。
"""

from __future__ import annotations

import asyncio
import logging

from app.adapters.embedding import embed_texts, embedding_enabled, embedding_model
from app.batch.runner import JobResult
from app.db import repo
from app.db.engine import get_engine

logger = logging.getLogger(__name__)

# 1 バッチで OpenAI 互換 embeddings API へまとめて投げる件数（embed_news と同じ上限感覚）。
_EMBED_BATCH = 100

# near_duplicate 判定の余弦距離しきい値（これ以下なら重複候補としてフラグ）。保守的既定・
# tunable（語彙の育ち方を見て調整する・ADR-050「閾値は定数・保守的既定・tunable」）。
_NEAR_DUP_MAX_DISTANCE = 0.15


def run() -> JobResult:
    """embedding が NULL/モデル不一致の themes 行を埋め込み near_dup を判定する（ADR-050/045）。

    embedding 未設定なら ok=True・rows=0 で静かに skip（ADR-006・ADR-045「未設定なら
    静かに機能オフ」）。1 バッチの埋め込み失敗は握って打ち切るが、failed_batches > 0 なら
    ok=False で返し runner の通知に乗せる（tag 系と契約対称・ADR-018・
    tasks/review-2026-06-12.md C-7。成功済み埋め込みは永続済みのまま残し翌晩に未埋め込み分
    だけ再試行＝自己回復性は維持）。near_dup 判定の失敗は握って degrade（候補提示は
    best-effort・ok には響かせない）。ジョブ境界の例外も握り ok=False で返す。
    """
    if not embedding_enabled():
        return JobResult(name="embed_themes", ok=True, rows=0, detail="embedding 未設定で skip")

    model = embedding_model()
    embedded = 0
    flagged = 0
    failed_batches = 0
    try:
        while True:
            with get_engine().connect() as conn:
                rows = repo.list_themes_needing_embedding(
                    conn, current_model=model, limit=_EMBED_BATCH
                )
            if not rows:
                break

            names = [str(r["name"]) for r in rows]
            try:
                vectors = asyncio.run(embed_texts(names))
            except Exception:  # noqa: BLE001 — 1 バッチの埋め込み失敗は握り打ち切る（ADR-018）
                logger.warning("embed_themes: 1 バッチの埋め込みに失敗（翌晩に再試行・ADR-050）")
                failed_batches += 1
                break  # 同じ行を再取得して無限ループしないよう打ち切る（embed_news 同型）

            if not vectors:
                break  # 機能オフ相当（None）/空。残りは次回に回す
            packed: list[tuple[str, bytes]] = []
            with get_engine().begin() as conn:  # 1 バッチ 1 begin（W2・embed_news 同型）
                for name, vec in zip(names, vectors, strict=True):
                    blob = repo.pack_embedding(vec)
                    repo.update_theme_embedding(conn, name, blob, model)
                    embedded += 1
                    packed.append((name, blob))

            # near_duplicate_of 判定（新規埋め込み分のみ・自動マージしない・ADR-050）。
            try:
                with get_engine().connect() as conn:
                    for name, blob in packed:
                        nearest = repo.find_nearest_theme(conn, name, blob)
                        distance = (nearest or {}).get("distance")
                        if distance is not None and float(distance) <= _NEAR_DUP_MAX_DISTANCE:
                            repo.set_theme_near_duplicate(name, str(nearest["name"]))  # type: ignore[index]
                            flagged += 1
                        else:
                            # 閾値超え/候補なしは None＝再埋め込み時の過去フラグ解除を兼ねる。
                            repo.set_theme_near_duplicate(name, None)
            except Exception:  # noqa: BLE001 — フラグ判定失敗は握って degrade（候補提示は best-effort）
                logger.warning("embed_themes: near_duplicate 判定に失敗（embedding は保存済み）")

            if len(rows) < _EMBED_BATCH:
                break  # 取り切った
    except Exception as exc:  # noqa: BLE001 — ジョブ境界で握り runner に返す
        logger.exception("embed_themes: 失敗")
        return JobResult(name="embed_themes", ok=False, rows=embedded, detail=str(exc))

    detail = f"テーマ埋め込み {embedded} 件・near_dup フラグ {flagged} 件"
    if failed_batches:
        detail += f"（{failed_batches} バッチ失敗・翌晩に再試行）"
    # 機能が有効なのに API 呼び出しが失敗した夜は ok=False（tag 系と契約対称・ADR-018・C-7）。
    return JobResult(name="embed_themes", ok=failed_batches == 0, rows=embedded, detail=detail)
