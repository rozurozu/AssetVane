"""FastAPI 本体。

AssetVane の唯一のデータ所有者（ADR-005）。Next.js からは REST 経由でのみ触る。
Phase 0: 死活監視 `/health`・銘柄/株価 API（/stocks・/quotes）・AI 最小チャット（/chat）。
Phase 1: シグナル一覧 API（/signals）・手動バッチ起動（/batch/run）・夜間 cron（APScheduler 同居）。
数理計算・全銘柄バッチは batch/・quant/ が担い、AI Tool は後続 Phase で足す。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI

from app.advisor import router as advisor_router
from app.advisor.tools.registry import CURRENT_PHASE
from app.batch import run_nightly
from app.config import settings
from app.db import repo
from app.db.engine import get_engine, healthcheck, init_db
from app.logging_config import setup_logging
from app.routers.advisor_state import router as advisor_state_router
from app.routers.assets import router as assets_router
from app.routers.batch import router as batch_router
from app.routers.cards import router as cards_router
from app.routers.diagnostics import router as diagnostics_router
from app.routers.dossier import router as dossier_router
from app.routers.edinet_config import router as edinet_config_router
from app.routers.edinetdb_config import router as edinetdb_config_router
from app.routers.funds import router as funds_router
from app.routers.general_news import router as general_news_router
from app.routers.jquants_config import router as jquants_config_router
from app.routers.lead_lag import router as lead_lag_router
from app.routers.llm_config import router as llm_config_router
from app.routers.news import router as news_router
from app.routers.portfolio import router as portfolio_router
from app.routers.profile import router as profile_router
from app.routers.screening_filters import router as screening_filters_router
from app.routers.signals import router as signals_router
from app.routers.stocks import router as stocks_router
from app.routers.us_holdings import router as us_holdings_router
from app.routers.us_stocks import router as us_stocks_router
from app.routers.watchlist import router as watchlist_router
from app.services.jquants_config import plan_status

# import 時に 1 回だけログ基盤を構成する（ADR-038）。uvicorn は app import 前に自前の
# LOGGING_CONFIG で dictConfig するため、ここで後勝ちに上書きしてテキスト形式/stdout に揃える。
setup_logging()

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

    try:
        yield
    finally:
        if scheduler is not None:
            # 実行中ジョブを待たずに止める（プロセス終了をブロックしない）。
            scheduler.shutdown(wait=False)


app = FastAPI(
    title="AssetVane API",
    version="0.1.0",
    description="日米株を分析し AI と投資方針を相談する単一ユーザー向けダッシュボードの API。",
    lifespan=lifespan,
)

# CORS は不要（ADR-037）。ブラウザは Next の同一オリジン `/api` だけを叩き、Next の rewrites が
# 裏で backend へ素通しするため、backend に届く時点で cross-origin ではない。別端末（PC・スマホ）
# から見る場合もブラウザの相手は frontend(:3000) だけなので CORSMiddleware は載せない。

# 銘柄・株価（Phase 0／docs/api.md §1）。GET /stocks・/quotes（routers/stocks.py）。
app.include_router(stocks_router)
# 米国株（Phase 7(B-1)／提示専用＝ADR-039(B)・ADR-055）。GET /us-stocks・/us-quotes。
app.include_router(us_stocks_router)
# 米株保有・取引（Phase 7(B-2)・ADR-057）。GET/POST/DELETE /us-holdings・/us-transactions。
app.include_router(us_holdings_router)
# シグナル一覧（Phase 1／spec §5.1）。GET /signals（routers/signals.py）。
app.include_router(signals_router)
# 手動バッチ起動（Phase 1／spec §3.8）。POST /batch/run（routers/batch.py）。
app.include_router(batch_router)
# 診断（ADR-011）。POST /diagnostics/discord-test（routers/diagnostics.py）。
app.include_router(diagnostics_router)
# AI Advisor（軸2・相談チャット）。POST /chat（advisor/router.py）。
app.include_router(advisor_router)
# AI Advisor 状態（Phase 3）。/policy・/journal・/proposals（routers/advisor_state.py）。
app.include_router(advisor_state_router)
# 投資家プロファイル（ADR-082）。GET/PUT /profile・GET /profile/notes（routers/profile.py）。
app.include_router(profile_router)
# ポートフォリオ（Phase 2）。GET /portfolios・GET /holdings・POST /transactions・metrics・optimize。
app.include_router(portfolio_router)
# 資産概要・現金・外部資産（Phase 2）。GET /cash・/external-assets・/asset-overview。
app.include_router(assets_router)
# 投資信託（ADR-054）。GET/POST/DELETE /funds・/fund-transactions・/fund-holdings・nav-series。
app.include_router(funds_router)
# スクリーニング保存フィルタ（ADR-031）。CRUD /screening-filters（routers/screening_filters.py）。
app.include_router(screening_filters_router)
# watchlist（Phase 4／spec §5.1）。GET/POST/DELETE /watchlist（routers/watchlist.py）。
app.include_router(watchlist_router)
# ドシエ（Phase 4／spec §5.2）。GET /dossiers/{code}・POST .../investigate（routers/dossier.py）。
app.include_router(dossier_router)
# 一般ニュース（ADR-034）。GET /general-news（routers/general_news.py）。
app.include_router(general_news_router)
# ニュース統合コーパス（ADR-046/047）。GET/POST/DELETE /news（routers/news.py）。
app.include_router(news_router)
# 日米業種リードラグ（Phase 7／SIG-FIN-036-13）。GET /lead-lag（routers/lead_lag.py）。
app.include_router(lead_lag_router)
# LLM プロバイダ複数登録・面別 provider/model 設定（ADR-058）。/llm/providers・/llm/faces。
app.include_router(llm_config_router)
# J-Quants 接続設定（api_key/plan を DB+WebUI で管理・ADR-061）。GET/PUT /jquants/config。
app.include_router(jquants_config_router)
app.include_router(edinetdb_config_router)
app.include_router(edinet_config_router)
# 知識カード（ADR-062）。GET/POST/PUT/DELETE /cards・triage・activate（routers/cards.py）。
app.include_router(cards_router)


@app.get("/health")
def health() -> dict[str, object]:
    """死活監視と必須環境変数の充足チェック（architecture.md §7.4）。

    llm_cost は ADR-028 コストガードの状態（画面バナーの判定材料・spec §7.1）。当月累計は
    sum_llm_cost_month で毎回算出する派生値で専用フラグは持たない（UTC 月＝llm.py と同算式）。
    jquants は右上バッジの動的化用のプラン状態（plan/delay_days/configured・ADR-061）。
    いずれも集計失敗は死活監視を巻き込まないよう握って安全な既定に倒す（best-effort）。
    """
    month_total = 0.0
    # 未登録＝Free 相当の 12 週遅延に倒す安全既定（DB 未読でも右上バッジが壊れない）。
    jquants: dict[str, object] = {"plan": "free", "delay_days": 84, "configured": False}
    try:
        with get_engine().connect() as conn:
            month_total = repo.sum_llm_cost_month(conn, datetime.now(UTC).strftime("%Y-%m"))
            jquants = plan_status(conn)
    except Exception:  # noqa: BLE001 — health を集計失敗で落とさない（best-effort）
        logger.exception("llm_cost / jquants 集計に失敗（health は続行）")
    return {
        "status": "ok",
        "service": "assetvane-backend",
        "version": app.version,
        # 投入フェーズは Tool ゲートの単一の真実 CURRENT_PHASE を参照する
        # （ハードコードしない＝tasks/review-2026-06-12.md C-9）。
        "phase": CURRENT_PHASE,
        "db": "ok" if healthcheck() else "error",
        "env": settings.env_status(),
        "llm_cost": {
            "mode": settings.llm_cost_guard_mode,
            "limit_usd": settings.llm_cost_limit_usd,
            "month_total_usd": month_total,
            "exceeded": month_total >= settings.llm_cost_limit_usd,
        },
        "jquants": jquants,
    }
