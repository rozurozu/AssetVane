"""夜間バッチ（cron から起動）。Phase 1 で導入する。

DB に触れる OS プロセスは FastAPI 1 つ（ADR-002/005）。夜間バッチはその中で
冪等・UPSERT のジョブ群として動く。設計: docs/architecture.md §3.1。
`run_nightly` / `run_jobs` / `JobResult` を re-export する（`from app.batch import run_nightly`）。
"""

from __future__ import annotations

from app.batch.runner import JobResult, run_jobs, run_nightly

__all__ = ["JobResult", "run_jobs", "run_nightly"]
