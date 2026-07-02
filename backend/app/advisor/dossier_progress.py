"""ドシエ調査の「今この銘柄を調査中か」を持つプロセスメモリ・レジストリ（ADR-076）。

なぜ要るか（ADR-076）: 「調査する」ボタンの調査中表示は frontend のローカル state だけで持ち、
リロードで失われていた（サーバは調査中という事実を一切記録していなかった）。リロードしても
「調査中」を保つには、`GET /dossiers/{code}` が「今この銘柄を調査中か」を返せる必要がある。

なぜ DB でなくプロセスメモリか（ADR-005/ADR-076・`batch/state.py` と同じ論拠）:
- DB に触れる OS プロセスは FastAPI 1 つに限定（ADR-005）。調査パイプライン（investigate_stock）も
  それを露出する GET も同一プロセスで走るので、メモリ・シングルトンで真偽が整合する。
- **プロセスが死ねばフラグも一緒に消える**＝クラッシュ/再起動/dev の `--reload` で「調査中が永遠に
  残る」ゴミが生まれない（DB 列にすると crash 時に stale なフラグが残り掃除が要る）。永続は不要。

参照カウント（`dict[str, int]`）で持つ理由（ADR-020）: 同一 code を夜間巡回と手動ボタンが同時に
調査しうる（銘柄単位ロックは無い＝ADR-020「1 パイプライン・複数起動口」）。単なる集合だと片方の
完了で誤って早期クリアしてしまうため、mark/unmark をカウントで数え、全ての調査が終わってから
`is_investigating` が False になるようにする。

スレッド安全性: GET は同期 def としてスレッドプールから、mark/unmark は event loop 上の async
ハンドラから触れるため、`threading.Lock` で dict の読み書きを囲む（`batch/state.py` と同流儀）。
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
# code -> 進行中の調査数（0 になったら pop してキーごと消す）。
_in_progress: dict[str, int] = {}


def mark(code: str) -> None:
    """`code` の調査開始を記録する（`investigate_stock` の冒頭で呼ぶ・参照カウント +1）。"""
    with _lock:
        _in_progress[code] = _in_progress.get(code, 0) + 1


def unmark(code: str) -> None:
    """`code` の調査終了を記録する（`investigate_stock` の finally で呼ぶ・参照カウント -1）。

    0 以下になったらキーごと消す（複数調査が同時に走っていても、全て終わるまで残る）。
    """
    with _lock:
        n = _in_progress.get(code, 0) - 1
        if n <= 0:
            _in_progress.pop(code, None)
        else:
            _in_progress[code] = n


def is_investigating(code: str) -> bool:
    """`code` が今調査中か（`GET /dossiers/{code}` が Dossier.investigating に載せる・ADR-076）。"""
    with _lock:
        return _in_progress.get(code, 0) > 0
