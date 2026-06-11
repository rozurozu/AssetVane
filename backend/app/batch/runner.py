"""夜間バッチのオーケストレーション（spec §3.3・ADR-002・ADR-011・ADR-018）。

「ロック取得 → ジョブを順に実行 → 結果を集約 → 失敗があれば Discord 通知 → ロック解放」。
各ジョブは独立・冪等・部分失敗から再開可能（`fetch_meta`）。`run_nightly()` は「毎晩 cron」と
「`POST /batch/run` 手動」の両口から同一プロセス・同一関数で呼ばれる（ADR-011「1つの脳・2つの
起動口」）。プロセス非依存に保ち、将来の専用 batch サービスへ移せるようにする（spec §3.7）。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from app.batch import lock, notify, state

logger = logging.getLogger(__name__)


def _format_elapsed(seconds: float) -> str:
    """経過秒を「h時間m分s秒」の人間可読文字列にする（終了ログの所要時間表示用）。"""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}時間{m}分{s}秒"
    if m:
        return f"{m}分{s}秒"
    return f"{s}秒"


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
    """夜間バッチ（NIGHTLY_JOBS 全体）を実行し JobResult のリストを返す（spec §3.3）。

    full_backfill=True: fetch_meta を無視して BACKFILL_YEARS 分を頭から取り直す（初回/復旧）。
    False（既定）: fetch_meta['daily_quotes'] の翌営業日から today まで差分取得。
    実体は run_jobs（脳の本体）に委譲する。NIGHTLY_JOBS の import は循環回避で関数内に置く。
    """
    from app.batch.jobs import NIGHTLY_JOBS

    return run_jobs(NIGHTLY_JOBS, label="夜間バッチ", full_backfill=full_backfill)


def run_jobs(
    jobs: list[object], *, label: str = "バッチ", full_backfill: bool = False
) -> list[JobResult]:
    """ジョブ列を順に実行し JobResult のリストを返す（脳の本体・ADR-011「1つの脳・2つの起動口」）。

    run_nightly（NIGHTLY 全体）と、部分ジョブ列の起動口（/settings の EDINET 差分タグ付け＝
    fetch_edinet_descriptions + tag_jp_themes）が共有する。`lock.acquire()` で囲み多重起動を防ぎ、
    ok=False が 1 件でもあれば notify.error() を 1 度だけ呼ぶ。ロック競合（BatchAlreadyRunning）は
    そのまま送出する（cron はログ・REST は 409 に翻訳）。

    実行状態は `state` モジュール（メモリ singleton・ADR-036）に映し、WebUI が `GET /batch/status`
    で「実行中・今どのジョブ・経過」を見られるようにする。停止は協調キャンセル＝各ジョブの**境界で**
    `state.should_stop()` を見て break する（今のジョブを終えてから止まる）。停止は意図的操作なので
    「正常終了」扱いとし、残ジョブは未実行のまま **Discord エラー通知は鳴らさない**（ADR-036）。
    """
    results: list[JobResult] = []
    stopped = False
    with lock.acquire():
        state.begin(full_backfill=full_backfill)
        started = time.monotonic()
        # バッチ全体の開始を 1 行で残す（cron/手動どちらの起動口でも同じ脳から出る・ADR-011）。
        # 「ここからここまで」を grep で追えるよう、対になる終了ログを後段で出す。
        logger.info(
            "%s開始: %s・ジョブ %d 件",
            label,
            "フル取得" if full_backfill else "差分取得",
            len(jobs),
        )
        try:
            for job in jobs:
                # ジョブ境界で停止要求を確認する（今のジョブを終えてから止まる・ADR-036）。
                if state.should_stop():
                    stopped = True
                    logger.info("バッチ停止要求を検知。残りジョブをスキップして正常終了する。")
                    break
                # __module__="app.batch.jobs.fetch_quotes" の末尾だけを表示用の短名にする。
                name = getattr(job, "__module__", job.__name__).rsplit(".", 1)[-1]
                state.set_current_job(name)
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
        finally:
            state.end()

    # バッチ全体の終了を 1 行で残す（開始ログと対・経過時間つき）。所要時間がログから直に読める。
    ok_count = sum(1 for r in results if r.ok)
    logger.info(
        "%s終了: %s・%d/%d ジョブ成功・経過 %s",
        label,
        "停止により中断" if stopped else "完走",
        ok_count,
        len(results),
        _format_elapsed(time.monotonic() - started),
    )

    # 停止（ユーザー操作）は失敗ではないので通知しない。通常完了時のみ失敗を集約通知（ADR-036）。
    if not stopped:
        failed = [r for r in results if not r.ok]
        if failed:
            detail = "\n".join(f"- {r.name}: {r.detail}" for r in failed)
            notify.error(f"{label}でジョブが失敗", detail)
    return results


def _invoke(job: object, *, full_backfill: bool) -> JobResult:
    """ジョブ関数を呼ぶ。full_backfill キーワードを受けるものだけに渡す（spec §3.3）。"""
    import inspect

    params = inspect.signature(job).parameters  # type: ignore[arg-type]
    if "full_backfill" in params:
        return job(full_backfill=full_backfill)  # type: ignore[operator]
    return job()  # type: ignore[operator]
