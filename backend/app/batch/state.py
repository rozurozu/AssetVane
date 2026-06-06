"""夜間バッチの実行状態（メモリ・単一プロセス前提＝ADR-005/011/036）。

WebUI に「今バッチが動いているか・どのジョブか・止めたいか」を見せるための軽量状態。
DB スキーマは増やさず FastAPI プロセス内のモジュール singleton で持つ（ADR-036）。バッチは
BackgroundTask（`POST /batch/run`）・APScheduler（cron）・CLI（`backfill --nightly`）の
いずれも**同一プロセス内**で走り（ADR-005）、プロセスが死ねば走行も状態も一緒に消えるので
`running` の真偽が常に整合する（DB 永続化が要らない理由）。

更新は `run_nightly()`（脳・ADR-011）の中だけで行い、起動口に依らず状態が映る。停止は
**協調キャンセル**＝`request_stop()` で `stop_requested` を立て、`run_nightly` のジョブループ
先頭が `should_stop()` を見て break する（今のジョブを終えてから止まる・ADR-036）。中断は
意図的操作なので「正常終了」扱いとし、Discord エラー通知は鳴らさない（runner 側の規律）。

スレッド安全性: BackgroundScheduler / BackgroundTasks は同期ジョブをスレッドプールで回し、
`GET /batch/status` は別スレッドから読む。素朴な属性アクセスでも GIL でほぼ安全だが、一貫した
スナップショットを返すため `threading.Lock` で囲む。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from datetime import UTC, datetime


@dataclass(frozen=True)
class BatchState:
    """バッチ実行状態のスナップショット（router がそのまま返す形・ADR-036）。

    running:        バッチが走行中か。
    current_job:    実行中ジョブの短名（例 "fetch_quotes"）。idle / 開始直後は None。
    started_at:     走行開始時刻（ISO8601・UTC）。idle なら None。
    full_backfill:  full（初回/復旧）か差分か。WebUI の表示用。
    stop_requested: 停止が要求済みか（次のジョブ境界で break する）。
    """

    running: bool = False
    current_job: str | None = None
    started_at: str | None = None
    full_backfill: bool = False
    stop_requested: bool = False


_lock = threading.Lock()
_state = BatchState()


def snapshot() -> BatchState:
    """現在の状態をスナップショットで返す（`GET /batch/status` 用）。"""
    with _lock:
        return _state


def begin(*, full_backfill: bool) -> None:
    """走行開始を記録する（`run_nightly` 冒頭で呼ぶ）。`stop_requested` も必ず初期化する。"""
    global _state
    with _lock:
        _state = BatchState(
            running=True,
            current_job=None,
            started_at=datetime.now(UTC).isoformat(timespec="seconds"),
            full_backfill=full_backfill,
            stop_requested=False,
        )


def set_current_job(name: str) -> None:
    """実行中ジョブの短名を更新する（各ジョブの直前で呼ぶ）。"""
    global _state
    with _lock:
        _state = replace(_state, current_job=name)


def request_stop() -> bool:
    """停止を要求する（`POST /batch/stop`）。走行中なら受理して True、idle なら False。"""
    global _state
    with _lock:
        if not _state.running:
            return False
        _state = replace(_state, stop_requested=True)
        return True


def should_stop() -> bool:
    """停止要求が立っているか（`run_nightly` のジョブループ先頭で見る）。"""
    with _lock:
        return _state.stop_requested


def end() -> None:
    """走行終了を記録し idle へ戻す（`run_nightly` の finally で呼ぶ）。"""
    global _state
    with _lock:
        _state = BatchState()
