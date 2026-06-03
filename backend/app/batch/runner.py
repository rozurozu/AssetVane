"""夜間バッチのオーケストレーション（spec §3.3・ADR-002・ADR-011・ADR-018）。

「ロック取得 → ジョブを順に実行 → 結果を集約 → 失敗があれば Discord 通知 → ロック解放」。
各ジョブは独立・冪等・部分失敗から再開可能（`fetch_meta`）。`run_nightly()` は「毎晩 cron」と
「`POST /batch/run` 手動」の両口から同一プロセス・同一関数で呼ばれる（ADR-011「1つの脳・2つの
起動口」）。プロセス非依存に保ち、将来の専用 batch サービスへ移せるようにする（spec §3.7）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.batch import lock, notify

logger = logging.getLogger(__name__)


@dataclass
class JobResult:
    """1 ジョブの実行結果（観測用・spec §3.3）。

    name: ジョブ名 / ok: 成功可否 / rows: 取得・UPSERT 行数 / detail: 進捗・エラーメッセージ。
    """

    name: str
    ok: bool
    rows: int
    detail: str


def run_nightly(*, full_backfill: bool = False) -> list[JobResult]:
    """夜間バッチを実行し JobResult のリストを返す（spec §3.3）。

    full_backfill=True: fetch_meta を無視して BACKFILL_YEARS 分を頭から取り直す（初回/復旧）。
    False（既定）: fetch_meta['daily_quotes'] の翌営業日から today まで差分取得。
    `lock.acquire()` で囲み、ok=False が 1 件でもあれば notify.error() を 1 度だけ呼ぶ。
    ロック競合（BatchAlreadyRunning）はそのまま送出する（cron はログ・/batch/run は 409 に翻訳）。
    """
    # jobs パッケージは JobResult を runner から import するため、循環回避で関数内 import する。
    from app.batch.jobs import NIGHTLY_JOBS

    results: list[JobResult] = []
    with lock.acquire():
        for job in NIGHTLY_JOBS:
            name = getattr(job, "__module__", job.__name__)
            try:
                # full_backfill を受けないジョブもあるため、シグネチャに応じて渡し分ける。
                result = _invoke(job, full_backfill=full_backfill)
            except Exception as exc:  # noqa: BLE001 — ジョブ単位で握り、後続ジョブを止めない
                logger.exception("ジョブ %s が例外で失敗", name)
                result = JobResult(name=name, ok=False, rows=0, detail=f"未捕捉例外: {exc}")
            results.append(result)
            logger.info(
                "ジョブ完了: %s ok=%s rows=%d detail=%s",
                result.name,
                result.ok,
                result.rows,
                result.detail,
            )

    failed = [r for r in results if not r.ok]
    if failed:
        detail = "\n".join(f"- {r.name}: {r.detail}" for r in failed)
        notify.error("夜間バッチでジョブが失敗", detail)
    return results


def _invoke(job: object, *, full_backfill: bool) -> JobResult:
    """ジョブ関数を呼ぶ。full_backfill キーワードを受けるものだけに渡す（spec §3.3）。"""
    import inspect

    params = inspect.signature(job).parameters  # type: ignore[arg-type]
    if "full_backfill" in params:
        return job(full_backfill=full_backfill)  # type: ignore[operator]
    return job()  # type: ignore[operator]
