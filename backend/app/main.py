"""FastAPI 本体。

AssetVane の唯一のデータ所有者（ADR-005）。Next.js からは REST 経由でのみ触る。
Phase 0 では死活監視 `/health` のみ。データ取得・計算・AI は後続 Phase で足す。
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings

app = FastAPI(
    title="AssetVane API",
    version="0.1.0",
    description="日米株を分析し AI と投資方針を相談する単一ユーザー向けダッシュボードの API。",
)

# 別端末（PC・スマホ）のブラウザから見るため CORS でフロントのオリジンを許可する。
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, object]:
    """死活監視と必須環境変数の充足チェック（architecture.md §7.4）。"""
    return {
        "status": "ok",
        "service": "assetvane-backend",
        "version": app.version,
        "phase": 0,
        "env": settings.env_status(),
    }
