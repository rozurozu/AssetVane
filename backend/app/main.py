"""FastAPI 本体。

AssetVane の唯一のデータ所有者（ADR-005）。Next.js からは REST 経由でのみ触る。
Phase 0: 死活監視 `/health`・銘柄/株価 API（/stocks・/quotes）・AI 最小チャット（/chat）。
全銘柄バッチ・cron・数理計算・AI Tool は後続 Phase で足す。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.advisor import router as advisor_router
from app.config import settings
from app.db.engine import healthcheck, init_db
from app.routers.stocks import router as stocks_router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 起動時にスキーマを用意（冪等＝CREATE TABLE IF NOT EXISTS 相当）。
    init_db()
    yield


app = FastAPI(
    title="AssetVane API",
    version="0.1.0",
    description="日米株を分析し AI と投資方針を相談する単一ユーザー向けダッシュボードの API。",
    lifespan=lifespan,
)

# 別端末（PC・スマホ）のブラウザから見るため CORS でフロントのオリジンを許可する。
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 銘柄・株価（Phase 0／docs/api.md §1）。GET /stocks・/quotes（routers/stocks.py）。
app.include_router(stocks_router)
# AI Advisor（軸2・相談チャット）。POST /chat（advisor/router.py）。
app.include_router(advisor_router)


@app.get("/health")
def health() -> dict[str, object]:
    """死活監視と必須環境変数の充足チェック（architecture.md §7.4）。"""
    return {
        "status": "ok",
        "service": "assetvane-backend",
        "version": app.version,
        "phase": 0,
        "db": "ok" if healthcheck() else "error",
        "env": settings.env_status(),
    }
