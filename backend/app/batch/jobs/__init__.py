"""夜間バッチのジョブ群（spec §3.3）。

`NIGHTLY_JOBS` が**実行順の単一の真実**。後続 Phase はここに append する。
順序の意図: マスタ → 日足取得 → シグナル計算（当日の事実が揃ってから算出）。
"""

from __future__ import annotations

from app.batch.jobs import calc_signals, fetch_quotes, sync_master

NIGHTLY_JOBS = [sync_master.run, fetch_quotes.run, calc_signals.run]
