"""dossier_progress レジストリ（調査の進行状態・参照カウント）の単体テスト（ADR-076）。

DB もネットも触らない純粋なプロセスメモリのふるまいを検証する（testing-strategy）。
参照カウントの要点＝同一 code を複数調査が同時に走っても、全て終わるまで investigating が True。
"""

from __future__ import annotations

from app.advisor import dossier_progress


def test_mark_unmark_refcount() -> None:
    """mark を 2 回・unmark を 1 回では調査中のまま、もう 1 回で False（参照カウント）。"""
    code = "9999"
    assert dossier_progress.is_investigating(code) is False

    # 夜間巡回と手動ボタンが同一 code を同時調査する状況を模す（ADR-020・銘柄ロック無し）。
    dossier_progress.mark(code)
    dossier_progress.mark(code)
    assert dossier_progress.is_investigating(code) is True

    dossier_progress.unmark(code)
    assert dossier_progress.is_investigating(code) is True  # まだ 1 件走っている

    dossier_progress.unmark(code)
    assert dossier_progress.is_investigating(code) is False  # 全て終わって落ちる


def test_unmark_without_mark_is_safe() -> None:
    """mark していない code の unmark は無害（カウントが負に沈まない）。"""
    code = "8888"
    dossier_progress.unmark(code)
    assert dossier_progress.is_investigating(code) is False
    # その後 mark→unmark が正常に効く（前の余計な unmark が状態を壊していない）。
    dossier_progress.mark(code)
    assert dossier_progress.is_investigating(code) is True
    dossier_progress.unmark(code)
    assert dossier_progress.is_investigating(code) is False
