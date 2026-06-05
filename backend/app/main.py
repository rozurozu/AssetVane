"""FastAPI 本体。

AssetVane の唯一のデータ所有者（ADR-005）。Next.js からは REST 経由でのみ触る。
Phase 0: 死活監視 `/health`・銘柄/株価 API（/stocks・/quotes）・AI 最小チャット（/chat）。
Phase 1: シグナル一覧 API（/signals）・手動バッチ起動（/batch/run）・夜間 cron（APScheduler 同居）。
数理計算・全銘柄バッチは batch/・quant/ が担い、AI Tool は後続 Phase で足す。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.advisor import router as advisor_router
from app.advisor.mcp_server import mount_mcp, session_manager_lifespan
from app.batch import run_nightly
from app.config import settings
from app.db.engine import healthcheck, init_db
from app.routers.advisor_state import router as advisor_state_router
from app.routers.assets import router as assets_router
from app.routers.batch import router as batch_router
from app.routers.dossier import router as dossier_router
from app.routers.portfolio import router as portfolio_router
from app.routers.screening_filters import router as screening_filters_router
from app.routers.signals import router as signals_router
from app.routers.stocks import router as stocks_router
from app.routers.watchlist import router as watchlist_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 起動時にスキーマを用意（冪等＝CREATE TABLE IF NOT EXISTS 相当）。
    init_db()

    # 夜間バッチ cron を FastAPI プロセスに同居させる（方式 C・追加コンテナ 0＝spec §3.7）。
    # dev の --reload 二重起動を避けるため既定 false でガードし、prod のみ true で起動する。
    scheduler = None
    if settings.batch_scheduler_enabled:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger

        # BackgroundScheduler は同期関数をスレッドプールで回す（run_nightly は同期 I/O）。
        scheduler = BackgroundScheduler()
        scheduler.add_job(
            run_nightly,
            CronTrigger(
                hour=settings.batch_cron_hour,
                minute=settings.batch_cron_minute,
                timezone=settings.batch_tz,
            ),
            max_instances=1,  # プロセス内の夜間ジョブを直列化（二重防御）
            coalesce=True,  # 取りこぼした起動はまとめて 1 回にする
        )
        scheduler.start()
        logger.info(
            "夜間バッチ cron を起動: %02d:%02d %s",
            settings.batch_cron_hour,
            settings.batch_cron_minute,
            settings.batch_tz,
        )

    # codex 接続用の MCP セッションマネージャを常駐させる（plans / ADR-012 の延長）。
    # provider="codex" のとき codex が /mcp 経由で自前 Tool を呼ぶ。openai 専用でも開いて無害。
    async with session_manager_lifespan():
        try:
            yield
        finally:
            if scheduler is not None:
                # 実行中ジョブを待たずに止める（プロセス終了をブロックしない）。
                scheduler.shutdown(wait=False)
            # codex 常駐 app-server を畳む（provider=codex で起動していれば。孤児化防止）。
            from app.advisor import codex_engine

            await codex_engine.shutdown()


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
# シグナル一覧（Phase 1／spec §5.1）。GET /signals（routers/signals.py）。
app.include_router(signals_router)
# 手動バッチ起動（Phase 1／spec §3.8）。POST /batch/run（routers/batch.py）。
app.include_router(batch_router)
# AI Advisor（軸2・相談チャット）。POST /chat（advisor/router.py）。
app.include_router(advisor_router)
# AI Advisor 状態（Phase 3）。/policy・/journal・/proposals（routers/advisor_state.py）。
app.include_router(advisor_state_router)
# ポートフォリオ（Phase 2）。GET /portfolios・GET /holdings・POST /transactions・metrics・optimize。
app.include_router(portfolio_router)
# 資産概要・現金・外部資産（Phase 2）。GET /cash・/external-assets・/asset-overview。
app.include_router(assets_router)
# スクリーニング保存フィルタ（ADR-031）。CRUD /screening-filters（routers/screening_filters.py）。
app.include_router(screening_filters_router)
# watchlist（Phase 4／spec §5.1）。GET/POST/DELETE /watchlist（routers/watchlist.py）。
app.include_router(watchlist_router)
# ドシエ（Phase 4／spec §5.2）。GET /dossiers/{code}・POST .../investigate（routers/dossier.py）。
app.include_router(dossier_router)

# codex 接続用 MCP（plans / ADR-012）。FastAPI 内に streamable HTTP の自前 Tool を立てる。
# DB に触れるのは FastAPI だけ（ADR-005）を保ちつつ codex に Tool を渡す。
mount_mcp(app)


@app.get("/health")
def health() -> dict[str, object]:
    """死活監視と必須環境変数の充足チェック（architecture.md §7.4）。"""
    return {
        "status": "ok",
        "service": "assetvane-backend",
        "version": app.version,
        "phase": 3,
        "db": "ok" if healthcheck() else "error",
        "env": settings.env_status(),
    }
