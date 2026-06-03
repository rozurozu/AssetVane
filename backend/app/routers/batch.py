"""手動バッチ起動の REST ルータ（Phase 1／docs/api.md・spec §3.8）。

POST /batch/run。cron（APScheduler 同居）と同じ `run_nightly()` を別口で叩く
（ADR-011「1つの脳・2つの起動口」）。初回バックフィルは約100〜150分かかり HTTP を
ブロックできないため、非同期受付の 202 を返す（裁定 L-2）。
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from app.batch import lock, run_nightly

router = APIRouter(tags=["batch"])


class BatchRunRequest(BaseModel):
    full_backfill: bool = False  # true で BACKFILL_YEARS 分を頭から取り直す（初回/復旧）


class BatchRunResponse(BaseModel):
    started: bool
    job_id: str | None = None


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

    background.add_task(run_nightly, full_backfill=req.full_backfill)
    return BatchRunResponse(started=True, job_id=None)
