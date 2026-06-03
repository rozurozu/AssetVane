"""AI Advisor 層（2 軸・LLM アダプタ・Tool Calling）。Phase 3 で実装する。

設計: docs/advisor.md / docs/decisions.md ADR-011〜016。
"""

from app.advisor.router import router

__all__ = ["router"]

# service / nightly は必要に応じて `from app.advisor import service` で参照する
# （ここで eager import すると router → service → router の循環を招くため re-export しない）。
