"""夜間バッチ: ニュース polarity 判定ジョブ（ADR-049/051・能動配信の前処理）。

設計の真実: docs/decisions.md ADR-049（定性タグのみ・数値スコアは作らない）・ADR-051
（能動配信）・batch-pattern。

NIGHTLY_JOBS の investigate_dossier.run（stock 層ニュースを書く）の後・notify_digest.run の前に
置く（embed_news の近辺）。stock 層で polarity 未判定（NULL）の行をまとめて 3 値 enum で判定し、
notify_digest の②保有銘柄悪材料アラートが当夜のニュースから polarity='negative' を拾えるようにする。

判定は LLM 単発（advisor/news_polarity.classify_polarities）越し（ADR-010/012）で、同期ジョブから
asyncio.run で駆動する（embed_news の流儀）。母集団は夜あたり天井（POLARITY_NIGHTLY_MAX）で頭打ちに
し（tag_us_themes と同思想）、LLM の 1 コールあたりは POLARITY_BATCH 件に分割する。母集団を一度に
取り切って Python でチャンク分割するのは、embed_news の while 再取得方式だと「LLM が一部記事だけ
enum 外を返し NULL のまま残る」行を同じ夜に取り直して無限ループしうるため（埋め込みは全行成功か
例外かの二択だが、polarity は部分的に enum 外がありうる）。溢れた分と壊れた応答で付かなかった行は
NULL のまま翌晩に拾う（自己回復性は維持）。

失敗の扱い（embed_news と契約対称・C-7・ADR-018）: 1 バッチの判定失敗（LLM 例外）は握って打ち切り、
バッチが全件 enum 外（総崩れ）は握って次バッチへ進む。いずれも failed_batches に数え、
failed_batches > 0 なら ok=False で返し runner の Discord 通知に乗せる（「黙って失敗を握りつぶさ
ない」）。**ただし tagger 面が未設定のときは沈黙 skip（ok=True・通知しない・ADR-058／#5）**＝
classify_polarities が伝播する FaceNotConfiguredError を「面未設定＝enrichment を静かに見送る」
シグナルとして扱い、LLM 総崩れ（空 dict＝通知する）と切り分ける（embed_news の embedding_enabled()
事前ガードと同型）。ジョブ境界の例外も握り ok=False で返す（後続ジョブを止めない）。
冪等性（ADR-002）: 判定
済み（polarity 非 NULL）の行は list_news_needing_polarity が返さないため、再実行しても二重判定は
起きない。
"""

from __future__ import annotations

import asyncio
import logging

from app.advisor.news_polarity import classify_polarities
from app.batch import state
from app.batch.runner import JobResult
from app.db import repo
from app.db.engine import get_engine
from app.services.llm_config import FaceNotConfiguredError

logger = logging.getLogger(__name__)

# 1 夜で判定する stock 層ニュースの上限（tag_us_themes の夜天井と同思想・溢れは翌晩）。
POLARITY_NIGHTLY_MAX = 200
# LLM 1 コールへまとめて投げる記事数（要約数行 × N がコンテキストに収まる範囲）。
POLARITY_BATCH = 25


def run() -> JobResult:
    """polarity 未判定の stock 層 news を 3 値で判定し、付けた件数を集約する（ADR-049/051）。

    母集団を list_news_needing_polarity で POLARITY_NIGHTLY_MAX 件まで取り、POLARITY_BATCH 件ずつ
    classify_polarities でまとめて判定 → update_news_polarity（バッチ単位の同一トランザクション）。
    1 バッチの LLM 例外は握って打ち切り、全件 enum 外（総崩れ）は握って次バッチへ進む。いずれも
    failed_batches に数え、failed_batches > 0 なら ok=False（embed_news と契約対称・ADR-018・C-7）。
    壊れた応答で付かなかった行・夜天井で溢れた行は NULL のまま翌晩再試行（自己回復性は維持）。
    ジョブ境界の例外も握り ok=False で返す。
    """
    tagged = 0
    failed_batches = 0
    try:
        with get_engine().connect() as conn:
            rows = repo.list_news_needing_polarity(conn, limit=POLARITY_NIGHTLY_MAX)
        if not rows:
            return JobResult(name="tag_news_polarity", ok=True, rows=0, detail="判定対象なし")

        # LLM を天井 200 件までバッチ判定するため長引きうる。バッチ境界で should_stop を見て中断する
        # （stop_aware＝最内ループ停止・ADR-036 追補／停止フラグはファイル＝ADR-070）。
        for start in state.stop_aware(range(0, len(rows), POLARITY_BATCH)):
            batch = rows[start : start + POLARITY_BATCH]
            try:
                results = asyncio.run(classify_polarities(batch))
            except FaceNotConfiguredError:
                # tagger 面が未設定＝沈黙 skip（enrichment・通知しない・ADR-018/058）。embed_news の
                # embedding_enabled() 事前ガードと同じ意味。ここで通知に乗せると未設定なだけで毎晩
                # Discord 誤アラートが飛ぶ（#5）。付いた分（あれば）は残し ok=True で静かに終える。
                logger.info("tag_news_polarity: tagger 面が未設定のため沈黙 skip（ADR-058）")
                return JobResult(
                    name="tag_news_polarity", ok=True, rows=tagged, detail="tagger 面未設定で skip"
                )
            except Exception:  # noqa: BLE001 — 1 バッチの判定失敗は握り打ち切る（ADR-018）
                logger.warning("tag_news_polarity: 1 バッチの判定に失敗（残りは翌晩・ADR-049）")
                failed_batches += 1
                break  # LLM 全体障害の可能性が高いので打ち切る（残りは翌晩に拾う）

            if not results:
                # 全件 enum 外/欠落（総崩れ）。NULL のまま翌晩再試行するが、黙って握りつぶさない
                # ため failed_batches に乗せて通知する（ADR-018）。次バッチは別の記事なので継続。
                logger.warning("tag_news_polarity: 1 バッチ全件 enum 外（翌晩再試行）")
                failed_batches += 1
                continue

            with get_engine().begin() as conn:
                for row in batch:
                    polarity = results.get(int(row["id"]))
                    if polarity is None:
                        continue  # 壊れた/欠落 → 書かず NULL のまま翌晩再試行
                    repo.update_news_polarity(conn, int(row["id"]), polarity)
                    tagged += 1
    except Exception as exc:  # noqa: BLE001 — ジョブ境界で握り runner に返す
        logger.exception("tag_news_polarity: 失敗")
        return JobResult(name="tag_news_polarity", ok=False, rows=tagged, detail=str(exc))

    detail = f"ニュース polarity 判定 {tagged} 件"
    if failed_batches:
        detail += f"（{failed_batches} バッチ失敗・翌晩に再試行）"
    return JobResult(name="tag_news_polarity", ok=failed_batches == 0, rows=tagged, detail=detail)
