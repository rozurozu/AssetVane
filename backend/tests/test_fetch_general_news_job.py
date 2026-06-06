"""夜間ジョブ fetch_general_news を検証する（ADR-034・batch-pattern）。

担保すること:
- adapter（fetch_general_news）をモックして DB に書く（LLM/ネットに出ない＝testing-strategy）。
- 取得記事が general_news へ UPSERT され JobResult.ok=True・rows が件数。
- 記事ゼロでも ok=True（好機が無い日もある）。
- adapter が例外でもジョブ境界で握り JobResult.ok=False（後続ジョブを止めない・ADR-018）。
investigate_dossier_job のモック流儀に倣う。DB は一時 SQLite（temp_db）。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.batch.jobs import fetch_general_news as job
from app.db import repo
from app.db.engine import get_engine


def _article(url: str, category: str) -> dict[str, Any]:
    return {
        "url": url,
        "title": f"{url} のタイトル",
        "summary": "要約。",
        "published_at": "2026-06-05",
        "source_type": "news",
        "extraction_status": "summarized",
        "category": category,
    }


def test_run_upserts_articles(temp_db, monkeypatch: pytest.MonkeyPatch) -> None:
    """取得記事が general_news へ入り ok=True・rows=件数。"""

    async def _fake() -> list[dict]:
        return [
            _article("https://a.example/1", "市況"),
            _article("https://a.example/2", "マクロ"),
        ]

    monkeypatch.setattr(job, "fetch_general_news", _fake)
    result = job.run()
    assert result.ok is True
    assert result.rows == 2
    with get_engine().connect() as conn:
        rows = repo.list_general_news(conn)
    assert len(rows) == 2
    assert {r["category"] for r in rows} == {"市況", "マクロ"}


def test_run_zero_articles_is_ok(temp_db, monkeypatch: pytest.MonkeyPatch) -> None:
    """記事ゼロでも ok=True（好機が無い日）。"""

    async def _empty() -> list[dict]:
        return []

    monkeypatch.setattr(job, "fetch_general_news", _empty)
    result = job.run()
    assert result.ok is True
    assert result.rows == 0


def test_run_adapter_failure_is_caught(temp_db, monkeypatch: pytest.MonkeyPatch) -> None:
    """adapter 例外はジョブ境界で握り ok=False（後続を止めない・ADR-018）。"""

    async def _boom() -> list[dict]:
        raise RuntimeError("取得失敗")

    monkeypatch.setattr(job, "fetch_general_news", _boom)
    result = job.run()
    assert result.ok is False
    assert "取得失敗" in result.detail
