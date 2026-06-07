"""夜間バッチ: セクターニュース取得ジョブ（ADR-044 (ii) セクター層）。

設計の真実: docs/decisions.md ADR-044・ADR-034・batch-pattern。

NIGHTLY_JOBS の `fetch_general_news.run` の直後・`run_advisor.run` の前で呼ばれる（軸1 夜の
分析AI が当日のセクター文脈を材料にできるよう、先に統合コーパスへ入れておく）。同期ジョブとして
JobResult を返す（runner.py の規律）。内部は fetch_general_news.py と同型で、`asyncio.run` で
非同期の取得パイプライン（adapters/news.fetch_sector_news）を駆動し、
`with get_engine().begin() as conn:` で統合ニュース表（news・level='sector'）へ atomic に
UPSERT する（W2）。

ADR-044: 取得前に直近 lookback 日の既存 url 集合（level='sector'）を DB から集め、adapter へ
known_urls として渡す（要約前 dedup＝既存分を再要約しない定常コスト削減）。adapter は DB 非依存
なので、DB 読み（known 収集）と DB 書き（UPSERT）はこのジョブが所有する（ADR-010）。

冪等性（ADR-002）: url UNIQUE ＋ on_conflict_do_nothing で再実行しても二重に入らない。
障害時（ADR-018）: 例外はジョブ境界で握り JobResult(ok=False) を返す（後続ジョブを止めない）。
業種単位の取得失敗は fetch_sector_news 内で握られる（1 業種失敗で全体を落とさない）。
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from datetime import UTC, datetime, timedelta

from app.adapters.general_news_config import SECTOR_NEWS_LOOKBACK_DAYS
from app.adapters.news import fetch_sector_news
from app.batch.runner import JobResult
from app.db import repo
from app.db.engine import get_engine

logger = logging.getLogger(__name__)


def run() -> JobResult:
    """セクターニュースを業種別に取得し統合コーパス（news・level='sector'）へ UPSERT する。

    既存 url 集合（known）を収集（ADR-044 dedup）→ 取得（async）→ begin() で束ねて UPSERT。
    記事ゼロでも ok=True（好機が無い日もある）。detail に業種別件数サマリを残す。例外は
    ジョブ境界で握り ok=False で返す（ADR-018）。
    """
    try:
        # ADR-044: 直近 lookback 日の既存 url を集め要約前 dedup の材料にする（DB 読みはジョブ）。
        lookback = datetime.now(UTC) - timedelta(days=SECTOR_NEWS_LOOKBACK_DAYS)
        since = lookback.strftime("%Y-%m-%d")
        with get_engine().connect() as conn:
            known = {r["url"] for r in repo.list_news(conn, level="sector", since=since)}

        articles = asyncio.run(fetch_sector_news(known))
        with get_engine().begin() as conn:
            n = repo.upsert_news(conn, articles)
    except Exception as exc:  # noqa: BLE001 — ジョブ境界で握り runner に返す
        logger.exception("fetch_sector_news: 失敗")
        return JobResult(name="fetch_sector_news", ok=False, rows=0, detail=str(exc))

    by_sector = Counter(a.get("category", "?") for a in articles)
    summary = "・".join(f"{label} {count}件" for label, count in by_sector.items()) or "0件"
    return JobResult(
        name="fetch_sector_news",
        ok=True,
        rows=n,
        detail=f"セクターニュース {n} 件取得（{summary}）",
    )
