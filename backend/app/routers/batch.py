"""手動バッチ起動・状態・停止の REST ルータ（Phase 1／docs/api.md・spec §3.8・ADR-036）。

POST /batch/run。cron（APScheduler 同居）と同じ `run_nightly()` を別口で叩く
（ADR-011「1つの脳・2つの起動口」）。初回バックフィルは約100〜150分かかり HTTP を
ブロックできないため、非同期受付の 202 を返す（裁定 L-2）。

GET /batch/status と POST /batch/stop（ADR-036）。WebUI が「実行中・今どのジョブ・経過」を
見て（status）、誤起動した初回フル等を止められる（stop＝協調キャンセル）。状態は `batch.state`
のメモリ singleton（単一プロセス前提＝ADR-005）に持つ。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from app.batch import lock, run_jobs, run_nightly, state

logger = logging.getLogger(__name__)

router = APIRouter(tags=["batch"])


def _guard_concurrent_start(fn: object, *args: object, **kwargs: object) -> None:
    """BackgroundTask 経路でバッチ起動関数を呼び、起動競合だけを握るラッパ（ADR-011/036）。

    受付時の「取得即解放」チェック（run_batch / run_edinet_differential）と、BackgroundTask が
    実際に走り出す瞬間の間に別バッチ（cron 等）が割り込むと、fn（run_nightly / run_jobs）が
    ロック再取得で `BatchAlreadyRunning` を送出する。BackgroundTask の未捕捉例外はスタック
    トレースとして「失敗」に見えるが、実体は「先行バッチが走っているので今回はスキップ」で
    実害がない（runner 本体は契約どおり競合を送出する＝REST は 409 翻訳・cron はログ）。
    起動競合だけを警告ログに倒して握る（ジョブ失敗は runner が JobResult/通知で扱う）。
    """
    try:
        fn(*args, **kwargs)  # type: ignore[operator]
    except lock.BatchAlreadyRunning:
        logger.warning("バッチ起動が他の実行と競合したためスキップした（既に実行中）。")


class BatchRunRequest(BaseModel):
    full_backfill: bool = False  # true で BACKFILL_YEARS 分を頭から取り直す（初回/復旧）


class BatchRunResponse(BaseModel):
    started: bool
    job_id: str | None = None


class BatchStatusResponse(BaseModel):
    """バッチ実行状態（ADR-036・batch.state のスナップショットと 1:1）。"""

    running: bool
    current_job: str | None = None  # 実行中ジョブの短名（idle / 開始直後は null）
    started_at: str | None = None  # 走行開始時刻（ISO8601・UTC）
    full_backfill: bool = False  # full（初回/復旧）か差分か
    stop_requested: bool = False  # 停止要求済みか（次のジョブ境界で止まる）


class BatchStopResponse(BaseModel):
    stopping: bool  # 停止要求を受理したか（実行中でなければ false）


@router.post("/batch/run", response_model=BatchRunResponse, status_code=202)
def run_batch(req: BatchRunRequest, background: BackgroundTasks) -> BatchRunResponse:
    """夜間バッチを非同期で起動し 202 を返す（spec §3.8）。

    起動前に flock を非ブロッキングで試し、取れなければ既にバッチ実行中なので 409 を返す。
    取れたら一旦解放し、BackgroundTasks で `run_nightly` を起動する（run_nightly 自身が
    再度ロックを取り直して走る）。進捗は fetch_meta.last_fetched_date / Discord で追う。
    """
    try:
        # 取得即解放で「今走っていないか」だけ確認する（実行は BackgroundTasks 側で取り直す）。
        with lock.acquire():
            pass
    except lock.BatchAlreadyRunning as exc:
        raise HTTPException(
            status_code=409,
            detail="バッチが既に実行中です。完了後に再度お試しください。",
        ) from exc

    background.add_task(_guard_concurrent_start, run_nightly, full_backfill=req.full_backfill)
    return BatchRunResponse(started=True, job_id=None)


@router.post("/edinet/run-differential", response_model=BatchRunResponse, status_code=202)
def run_edinet_differential(background: BackgroundTasks) -> BatchRunResponse:
    """EDINET 差分タグ付け（取得→cap タグ付け）を非同期で起動し 202 を返す（段階C・ADR-056）。

    /settings からのオンデマンド起動口。夜間と同じ差分（fetch_edinet_descriptions →
    tag_jp_themes）を run_jobs で回す（ADR-011「1つの脳・2つの起動口」＝run_nightly と同じ
    lock/state/通知機構を共有）。重い 15ヶ月バックフィルと無キャップ一括タグは app.scripts 手動の
    まま（コストガード・grill 2026-06-11）。進捗は既存 `GET /batch/status` で見る。

    起動前に flock を非ブロッキングで試し、取れなければ既に実行中なので 409（run_batch 同型）。
    """
    # 部分ジョブ列の起動口。NIGHTLY_JOBS 全体の import 循環を避けるため関数内 import する。
    from app.batch.jobs import fetch_edinet_descriptions, tag_jp_themes

    try:
        with lock.acquire():
            pass
    except lock.BatchAlreadyRunning as exc:
        raise HTTPException(
            status_code=409,
            detail="バッチが既に実行中です。完了後に再度お試しください。",
        ) from exc

    background.add_task(
        _guard_concurrent_start,
        run_jobs,
        [fetch_edinet_descriptions.run, tag_jp_themes.run],
        label="EDINET 差分タグ付け",
    )
    return BatchRunResponse(started=True, job_id=None)


@router.get("/batch/status", response_model=BatchStatusResponse)
def batch_status() -> BatchStatusResponse:
    """現在のバッチ実行状態を返す（ADR-036）。WebUI がポーリングして進捗・停止可否を出す。

    cron 起動・`POST /batch/run` 裏ジョブ・CLI `--nightly` のどの口で走っていても、`run_nightly`
    が更新する同じメモリ状態を映す（ADR-011「1つの脳」）。idle なら running=false で他は既定値。
    """
    s = state.snapshot()
    return BatchStatusResponse(
        running=s.running,
        current_job=s.current_job,
        started_at=s.started_at,
        full_backfill=s.full_backfill,
        stop_requested=s.stop_requested,
    )


@router.post("/batch/stop", response_model=BatchStopResponse)
def batch_stop() -> BatchStopResponse:
    """走行中バッチに停止を要求する（協調キャンセル・ADR-036）。

    `state.request_stop()` で `stop_requested` を立てるだけ。実体は `run_nightly` が**次のジョブ
    境界**で検知して break する（今のジョブを終えてから止まる＝強制 kill はしない）。
    走行中でなければ受理せず stopping=false を返す（差分・フルどちらの走行でも効く）。
    """
    return BatchStopResponse(stopping=state.request_stop())
