"""夜間ジョブ fetch_sector_news を検証する（ADR-044 (ii) セクター層・ADR-053・batch-pattern）。

担保すること:
- adapter（fetch_sector_news）をモックして DB に書く（LLM/ネットに出ない＝testing-strategy）。
- 取得記事が統合コーパス（news・level='sector'）へ UPSERT され JobResult.ok=True・rows が件数。
  sector17_code は J-Quants S17 体系 "1".."17"（ADR-053。stocks.sector17_code と同体系）。
- adapter には DB から集めた known_urls（既存 url 集合・level='sector'）が渡る（ADR-044 dedup）。
- 記事ゼロでも ok=True（好機が無い日もある）。
- adapter が例外でもジョブ境界で握り JobResult.ok=False（後続ジョブを止めない・ADR-018）。
fetch_general_news_job のモック流儀に倣う。DB は一時 SQLite（temp_db）。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.batch.jobs import fetch_sector_news as job
from app.db import repo
from app.db.engine import get_engine


def _article(url: str, sector17_code: str, category: str) -> dict[str, Any]:
    return {
        "url": url,
        "title": f"{url} のタイトル",
        "summary": "要約。",
        "published_at": "2026-06-05",
        "source_type": "news",
        "extraction_status": "summarized",
        # ADR-044: adapter が付与する統合タグ（セクター層）。
        "level": "sector",
        "sector17_code": sector17_code,
        "category": category,
        "source": "news",
    }


def test_run_upserts_articles(temp_db, monkeypatch: pytest.MonkeyPatch) -> None:
    """取得記事が統合コーパス（level='sector'）へ入り ok=True・rows=件数。"""

    async def _fake(known_urls: set[str] | None = None) -> list[dict]:
        return [
            _article("https://a.example/1", "1", "食品"),
            _article("https://a.example/2", "6", "自動車・輸送機"),
        ]

    monkeypatch.setattr(job, "fetch_sector_news", _fake)
    result = job.run()
    assert result.ok is True
    assert result.rows == 2
    with get_engine().connect() as conn:
        rows = repo.list_news(conn, level="sector")
    assert len(rows) == 2
    assert {r["sector17_code"] for r in rows} == {"1", "6"}  # S17 体系（ADR-053）
    assert all(r["level"] == "sector" for r in rows)


def test_run_passes_known_urls(temp_db, monkeypatch: pytest.MonkeyPatch) -> None:
    """既存 url（level='sector'）が known_urls として adapter に渡る（ADR-044 dedup）。"""
    from datetime import UTC, datetime

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    with get_engine().begin() as conn:
        repo.upsert_news(
            conn,
            [
                {
                    "url": "https://existing.example/1",
                    "title": "既存",
                    "summary": "要約。",
                    "published_at": today,
                    "level": "sector",
                    "sector17_code": "1",
                    "category": "食品",
                    "source": "news",
                    "extraction_status": "summarized",
                }
            ],
        )

    seen: dict[str, Any] = {}

    async def _capture(known_urls: set[str] | None = None) -> list[dict]:
        seen["known"] = known_urls
        return []

    monkeypatch.setattr(job, "fetch_sector_news", _capture)
    result = job.run()
    assert result.ok is True
    assert seen["known"] == {"https://existing.example/1"}


def test_run_zero_articles_is_ok(temp_db, monkeypatch: pytest.MonkeyPatch) -> None:
    """記事ゼロでも ok=True（好機が無い日）。"""

    async def _empty(known_urls: set[str] | None = None) -> list[dict]:
        return []

    monkeypatch.setattr(job, "fetch_sector_news", _empty)
    result = job.run()
    assert result.ok is True
    assert result.rows == 0


def test_run_adapter_failure_is_caught(temp_db, monkeypatch: pytest.MonkeyPatch) -> None:
    """adapter 例外はジョブ境界で握り ok=False（後続を止めない・ADR-018）。"""

    async def _boom(known_urls: set[str] | None = None) -> list[dict]:
        raise RuntimeError("取得失敗")

    monkeypatch.setattr(job, "fetch_sector_news", _boom)
    result = job.run()
    assert result.ok is False
    assert "取得失敗" in result.detail
