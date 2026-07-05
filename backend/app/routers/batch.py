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

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import Connection

from app.batch import lock, run_jobs, run_nightly, state
from app.db.engine import get_conn

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


def _reject_if_running() -> None:
    """flock を「取得即解放」で試し、既に走行中なら 409 を送出する受付ゲート（#20）。

    run_batch / run_edinet_differential が BackgroundTask 投入前に共有する（実行は
    BackgroundTask 側で lock を取り直す）。競合窓は `_guard_concurrent_start` が握る。
    """
    try:
        with lock.acquire():
            pass
    except lock.BatchAlreadyRunning as exc:
        raise HTTPException(
            status_code=409,
            detail="バッチが既に実行中です。完了後に再度お試しください。",
        ) from exc


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
    stopping: bool  # 停止要求を受理したか（ADR-070 で running ゲート撤廃＝常に true）


@router.post("/batch/run", response_model=BatchRunResponse, status_code=202)
def run_batch(req: BatchRunRequest, background: BackgroundTasks) -> BatchRunResponse:
    """夜間バッチを非同期で起動し 202 を返す（spec §3.8）。

    起動前に flock を非ブロッキングで試し、取れなければ既にバッチ実行中なので 409 を返す。
    取れたら一旦解放し、BackgroundTasks で `run_nightly` を起動する（run_nightly 自身が
    再度ロックを取り直して走る）。進捗は fetch_meta.last_fetched_date / Discord で追う。
    """
    _reject_if_running()  # 取得即解放で走行中かだけ確認（実行は BackgroundTasks 側で取り直す）
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

    _reject_if_running()  # run_batch 同型の受付ゲート（#20）
    background.add_task(
        _guard_concurrent_start,
        run_jobs,
        [fetch_edinet_descriptions.run, tag_jp_themes.run],
        label="EDINET 差分タグ付け",
    )
    return BatchRunResponse(started=True, job_id=None)


@router.post("/valuation/backfill-net-cash", response_model=BatchRunResponse, status_code=202)
def backfill_net_cash(
    background: BackgroundTasks, conn: Connection = Depends(get_conn)
) -> BatchRunResponse:
    """清原式ネットキャッシュの全銘柄バックフィルを非同期で起動し 202 を返す（ADR-083）。

    /settings の「全銘柄取得ボタン」からのオンデマンド起動口。sec_code→edinet_code の全件スイープ
    （resolve_edinet_codes）→ 全普通株の net_cash 焼き込み（calc_receivables_inventory）を
    full_backfill=True で run_jobs に回す（ADR-011「1つの脳・2つの起動口」＝run_nightly と同じ
    lock/state/通知を共有）。full_backfill はソフトキャップを日次予算まで上げ、net_cash 未焼き
    （NULL）を数日で埋める。1 回で日次予算まで焼いて中断し、翌日以降 or 夜間の差分運転が続きを焼く。

    **free では拒否**（日100/月600 で全4000銘柄を焼き切れず非現実的＝ADR-083）。未設定も拒否。
    起動前に flock を非ブロッキングで試し、取れなければ既に実行中なので 409（run_batch 同型）。
    """
    from app.batch.jobs import calc_receivables_inventory, resolve_edinet_codes
    from app.services.edinetdb_config import current_plan, resolve_edinetdb_config

    if resolve_edinetdb_config(conn) is None:
        raise HTTPException(
            status_code=400,
            detail="EDINET DB が未設定です。/settings の「EDINET DB 設定」で登録してください。",
        )
    if current_plan(conn) == "free":
        raise HTTPException(
            status_code=400,
            detail=(
                "全銘柄取得は pro プランが必要です（free の日100/月600 では全銘柄を焼けません）。"
                "/settings の「EDINET DB 設定」で pro に切り替えて実行してください。以降の更新は "
                "free でも決算開示があった銘柄だけ差分取得されます。"
            ),
        )

    _reject_if_running()  # run_batch 同型の受付ゲート（#20）
    background.add_task(
        _guard_concurrent_start,
        run_jobs,
        [resolve_edinet_codes.run, calc_receivables_inventory.run],
        label="清原式ネットキャッシュ 全銘柄取得",
        full_backfill=True,
    )
    return BatchRunResponse(started=True, job_id=None)


@router.get("/batch/status", response_model=BatchStatusResponse)
def batch_status() -> BatchStatusResponse:
    """現在のバッチ実行状態を返す（ADR-036/070）。WebUI がポーリングして進捗・停止可否を出す。

    running/current_job/started_at/full_backfill は**このプロセスのメモリ**（best-effort）。cron・
    `POST /batch/run` 裏ジョブは同一プロセスなので映るが、CLI `--nightly` や dev の `--reload` で
    分裂した別プロセスの走行は映らない（running=false に見える・ADR-070 state.py 参照）。一方
    `stop_requested` は停止ファイル（`data/batch.stop`）由来でクロスプロセスに正しい。idle なら
    running=false で他は既定値。
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
    """走行中バッチに停止を要求する（協調キャンセル・ADR-036/070）。

    `state.request_stop()` が停止ファイル（`data/batch.stop`）を touch するだけ。実体は
    `run_nightly` が**次のジョブ境界**と長尺ジョブの最内ループ（stop_aware）で検知して break する
    （今の単位を終えてから止まる＝強制 kill はしない）。ADR-070 で running ゲートを撤廃し常に
    stopping=true を返す（`--reload`/CLI で前面プロセスの running=false でも停止を届かせるため。
    idle 中の stray な要求は次の begin() が回収する）。差分・フルどちらの走行でも効く。
    """
    return BatchStopResponse(stopping=state.request_stop())
