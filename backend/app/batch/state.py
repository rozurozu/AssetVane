"""夜間バッチの実行状態と停止（ADR-005/011/036・停止のファイル化＝ADR-070）。

WebUI に「今バッチが動いているか・どのジョブか・止めたいか」を見せるための軽量状態。
2 つの関心を **別の器**に置く（ADR-070 が ADR-036 の保存方式を改訂した理由）:

- **status（running / current_job / started_at / full_backfill）＝プロセスメモリ**（ADR-036）。
  バッチは BackgroundTask（`/batch/run`）・APScheduler（cron）・CLI（`--nightly`）のいずれも
  同一プロセス内で走り（ADR-005）、プロセスが死ねば走行も表示も一緒に消えるので `running` の真偽が
  整合する＝DB 永続化は要らない。ただし **best-effort**＝dev の `--reload` で走行中バッチが古い
  プロセスに取り残される（orphan）と、停止を受ける前面プロセスの status は running=false に見える
  （その時は直 API で `POST /batch/stop` を叩く運用・ADR-070）。

- **停止フラグ（stop_requested）＝`data/batch.stop` ファイル**（ADR-070＝ADR-036 の改訂）。
  相互排他はもう `batch.lock` の `fcntl.flock`＝クロスプロセスなのに、停止だけメモリに閉じていた。
  dev の `--reload` は走行中バッチを古いプロセスに残したまま新プロセスを立て、`POST /batch/stop` は
  新プロセスのメモリに旗を立てるので走行中バッチに一生届かなかった（CLI 起動も別プロセスで最初から
  届かない）。停止をファイルに出すと、ロックと同じ土俵で **reload・編集・CLI のどれでも届く**。

**ライフサイクルの不変条件**: 停止ファイルの生成（touch）はロック外の `request_stop()` から・
消去（unlink）は **flock 保持中の `begin()`/`end()` だけ**（runner の `with lock.acquire()` 内）。
これで「走行中バッチには必ず届く／idle 中の取りこぼし（stray な停止要求）は次の begin() が回収する」
を両立する。起動時クリアはしない（orphan 宛の停止要求を誤消去しかねないため）。

停止は**協調キャンセル**＝`run_nightly` のジョブ境界と、長尺ジョブの最内ループ（`stop_aware`）が
`should_stop()`（＝ファイルの存在）を見て break する（今の単位を終えてから止まる＝強制 kill は
しない）。中断は意図的操作なので「正常終了」扱いとし、エラー通知は鳴らさない（runner 規律）。

スレッド安全性: status はスレッドプールから読まれるため `threading.Lock` で囲む。停止ファイルの
存在チェックはアトミックな stat 1 回で、ロック不要。
"""

from __future__ import annotations

import threading
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from app.config import settings


@dataclass(frozen=True)
class BatchState:
    """バッチ実行状態のスナップショット（router がそのまま返す形・ADR-036/070）。

    running:        バッチが走行中か（メモリ・best-effort）。
    current_job:    実行中ジョブの短名（例 "fetch_quotes"）。idle / 開始直後は None（メモリ）。
    started_at:     走行開始時刻（ISO8601・UTC）。idle なら None（メモリ）。
    full_backfill:  full（初回/復旧）か差分か。WebUI の表示用（メモリ）。
    stop_requested: 停止が要求済みか。真実源は `data/batch.stop` ファイルで、snapshot() が読む
                    （別プロセスから止められた場合も status に映る・ADR-070）。
    """

    running: bool = False
    current_job: str | None = None
    started_at: str | None = None
    full_backfill: bool = False
    stop_requested: bool = False


_lock = threading.Lock()
_state = BatchState()


def _stop_path() -> Path:
    """停止フラグファイルのパス（DB と同じ data/ 配下・`batch.lock` の兄弟＝ADR-070）。"""
    return Path(settings.database_path).resolve().parent / "batch.stop"


def _clear_stop() -> None:
    """停止フラグファイルを消す（flock 保持中の begin()/end() からのみ呼ぶ・ADR-070）。"""
    _stop_path().unlink(missing_ok=True)


def snapshot() -> BatchState:
    """現在の status スナップショットを返す（`GET /batch/status` 用）。

    running/current_job 等はメモリ（best-effort）だが、`stop_requested` は真実源の停止ファイルを
    見て返す（別プロセスから止められた場合も status に映る・ADR-070）。
    """
    with _lock:
        base = _state
    return replace(base, stop_requested=_stop_path().exists())


def begin(*, full_backfill: bool) -> None:
    """走行開始を記録する（`run_jobs` 冒頭・flock 内で呼ぶ）。前回の停止要求を必ず消す。

    停止ファイルの消去は flock 保持中の begin()/end() だけ（ADR-070 の不変条件）。これで idle 中に
    立った stray な停止要求が次の走行を即座に殺すことはない（begin で回収）。
    """
    _clear_stop()
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
    """停止を要求する（`POST /batch/stop`）。停止ファイルを生成し常に True を返す（ADR-070）。

    旧実装はメモリの running を見て「走行中のみ受理」だったが、`--reload` で分裂した別プロセスや
    CLI 起動では停止を受ける前面プロセスの running=false になり、旗が走行中バッチに届かなかった。
    停止をクロスプロセスなファイル（`batch.lock` と同じ土俵）に出し、running ゲートを撤廃して
    **常に書く**。idle 中の stray な要求は次の begin() が回収する（ADR-070 の不変条件）。
    """
    path = _stop_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return True


def should_stop() -> bool:
    """停止要求が立っているか（ジョブ境界／`stop_aware` の最内ループで見る・ADR-070）。

    真実源は `data/batch.stop` ファイルの存在。メモリを見ないので、reload/CLI で別プロセスから
    立てられた停止でも走行中バッチに届く。
    """
    return _stop_path().exists()


def stop_aware[T](iterable: Iterable[T]) -> Iterator[T]:
    """停止を見ながら反復する（長尺ジョブの最内ループ用・ADR-036 追補／ADR-070）。

    各要素を yield する**前**に `should_stop()` を見て、立っていればそこで打ち切る（今処理した要素
    までで止まる＝各反復で UPSERT＋`fetch_meta` 前進が済んでいれば「取れた分まで」永続化＋冪等
    再開）。ジョブ側は「停止で打ち切ったか」を必要とするなら、ループ後に `should_stop()` を見て
    detail に「停止により中断」を添える（実例＝fetch_quotes）。全ユニバース走査・cap 付きでも数分
    かかる LLM/embed 系に一律で被せられる（helper 化でコストがほぼゼロ＝ADR-070）。
    """
    for item in iterable:
        if should_stop():
            return
        yield item


def end() -> None:
    """走行終了を記録し idle へ戻す（`run_jobs` の finally・flock 内）。停止ファイルも消す。"""
    _clear_stop()
    global _state
    with _lock:
        _state = BatchState()
