"""夜間バッチ（cron から起動）。Phase 1 で導入する。

唯一の DB 書き手（ADR-002）。冪等・UPSERT。設計: docs/architecture.md §3.1。
`run_nightly` / `JobResult` を re-export し、`from app.batch import run_nightly` で使える。
"""

from __future__ import annotations

from app.batch.runner import JobResult, run_nightly

__all__ = ["JobResult", "run_nightly"]
