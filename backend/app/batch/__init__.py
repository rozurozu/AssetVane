"""夜間バッチ（cron から起動）。Phase 1 で導入する。

唯一の DB 書き手（ADR-002）。冪等・UPSERT。設計: docs/architecture.md §3.1。
"""
