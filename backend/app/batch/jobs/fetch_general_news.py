"""夜間バッチ: 一般ニュース取得ジョブ（市況層・ADR-034 → ADR-044）。

設計の真実: docs/decisions.md ADR-034・ADR-044・batch-pattern。

NIGHTLY_JOBS の `run_advisor.run` の**直前**で呼ばれる（軸1 夜の分析AI が当日の市況文脈を
材料にできるよう、先に一般ニュースを統合コーパスへ入れておく）。同期ジョブとして JobResult を返す
（runner.py の規律）。内部は investigate_dossier.py の流儀に倣い、`asyncio.run` で非同期の
取得パイプライン（adapters/news.fetch_general_news）を駆動し、
`with get_engine().begin() as conn:` で統合ニュース表（news・level='market'）へ atomic に
UPSERT する（W2）。

ADR-044: 取得前に直近 lookback 日の既存 url 集合（level='market'）を DB から集め、adapter へ
known_urls として渡す（要約前 dedup＝既存分を再要約しない定常コスト削減）。adapter は DB 非依存
なので、DB 読み（known 収集）と DB 書き（UPSERT）はこのジョブが所有する（ADR-010）。

冪等性（ADR-002）: url UNIQUE ＋ on_conflict_do_nothing で再実行しても二重に入らない。
障害時（ADR-018）: 例外はジョブ境界で握り JobResult(ok=False) を返す（後続ジョブを止めない）。
カテゴリ単位の取得失敗は fetch_general_news 内で握られる（1 カテゴリ失敗で全体を落とさない）。
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from datetime import UTC, datetime, timedelta

from app.adapters.general_news_config import GENERAL_NEWS_LOOKBACK_DAYS
from app.adapters.news import fetch_general_news
from app.batch.runner import JobResult
from app.db import repo
from app.db.engine import get_engine

logger = logging.getLogger(__name__)


def run() -> JobResult:
    """一般ニュースをカテゴリ別に取得し統合コーパス（news・level='market'）へ UPSERT する。

    既存 url 集合（known）を収集（ADR-044 dedup）→ 取得（async）→ begin() で束ねて UPSERT。
    記事ゼロでも ok=True（好機が無い日もある）。detail にカテゴリ別件数サマリを残す。例外は
    ジョブ境界で握り ok=False で返す（ADR-018）。
    """
    try:
        # ADR-044: 直近 lookback 日の既存 url を集め要約前 dedup の材料にする（DB 読みはジョブ）。
        lookback = datetime.now(UTC) - timedelta(days=GENERAL_NEWS_LOOKBACK_DAYS)
        since = lookback.strftime("%Y-%m-%d")
        with get_engine().connect() as conn:
            known = {r["url"] for r in repo.list_news(conn, level="market", since=since)}

        articles = asyncio.run(fetch_general_news(known))
        with get_engine().begin() as conn:
            n = repo.upsert_news(conn, articles)
    except Exception as exc:  # noqa: BLE001 — ジョブ境界で握り runner に返す
        logger.exception("fetch_general_news: 失敗")
        return JobResult(name="fetch_general_news", ok=False, rows=0, detail=str(exc))

    by_category = Counter(a.get("category", "?") for a in articles)
    summary = "・".join(f"{label} {count}件" for label, count in by_category.items()) or "0件"
    return JobResult(
        name="fetch_general_news",
        ok=True,
        rows=n,
        detail=f"一般ニュース {n} 件取得（{summary}）",
    )
