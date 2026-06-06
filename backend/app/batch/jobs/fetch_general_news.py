"""夜間バッチ: 一般ニュース取得ジョブ（銘柄に紐づかない別系統・ADR-034）。

設計の真実: docs/decisions.md ADR-034・grill-me 合意（3-adr-034-floofy-hoare）・batch-pattern。

NIGHTLY_JOBS の `run_advisor.run` の**直前**で呼ばれる（軸1 夜の分析AI が当日の市況文脈を
材料にできるよう、先に一般ニュースを台帳へ入れておく）。同期ジョブとして JobResult を返す
（runner.py の規律）。内部は investigate_dossier.py の流儀に倣い、`asyncio.run` で非同期の
取得パイプライン（adapters/news.fetch_general_news）を駆動し、
`with get_engine().begin() as conn:` で general_news へ atomic に UPSERT する（W2）。

冪等性（ADR-002）: url UNIQUE ＋ on_conflict_do_nothing で再実行しても二重に入らない。
障害時（ADR-018）: 例外はジョブ境界で握り JobResult(ok=False) を返す（後続ジョブを止めない）。
カテゴリ単位の取得失敗は fetch_general_news 内で握られる（1 カテゴリ失敗で全体を落とさない）。
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter

from app.adapters.news import fetch_general_news
from app.batch.runner import JobResult
from app.db import repo
from app.db.engine import get_engine

logger = logging.getLogger(__name__)


def run() -> JobResult:
    """一般ニュースをカテゴリ別に取得し general_news へ UPSERT する（ADR-034）。

    取得（async）→ begin() で束ねて UPSERT。記事ゼロでも ok=True（好機が無い日もある）。
    detail にカテゴリ別件数サマリを残す。例外はジョブ境界で握り ok=False で返す（ADR-018）。
    """
    try:
        articles = asyncio.run(fetch_general_news())
        with get_engine().begin() as conn:
            n = repo.upsert_general_news(conn, articles)
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
