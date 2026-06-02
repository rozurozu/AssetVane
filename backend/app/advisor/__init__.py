"""AI Advisor 層（2 軸・LLM アダプタ・Tool Calling）。Phase 3 で実装する。

設計: docs/advisor.md / docs/decisions.md ADR-011〜016。
"""

from app.advisor.router import router

__all__ = ["router"]
