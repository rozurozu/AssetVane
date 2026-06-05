"""Phase 4 ドシエ調査パイプライン（investigate_stock / summarize_dossier）を検証する。

担保すること（phase4-spec §3・§8・ADR-020/014）:
- URL 重複排除: 既存 url の記事は二重取り込みしない（n_sources_added・行数で確認）。
- 本文非保存: パイプラインが台帳へ渡すのは summary/url 等のみで全文を渡さない（ADR-020）。
- last_investigated_at / updated_at が前進する。
- mode が fetch_news に正しく渡る（"nightly"/"chat" 経路）。
- summarize_dossier に渡るのが「記事の要約と財務事実のみ」で、全文を載せない（ADR-014）。

LLM（engine.generate_once）と fetch_news は必ずモック（ネットを叩かない＝testing-strategy）。
DB は一時 SQLite。dossier は provider 解決を engine.generate_once（遅延 import）経由で呼ぶため、
モックは engine モジュール側に当てる。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.advisor import dossier, engine
from app.db import repo
from app.db.engine import get_engine

STOCK = {
    "code": "7203",
    "company_name": "トヨタ自動車",
    "sector33_code": "3700",
    "sector17_code": "6",
    "market_code": "0111",
    "is_etf": 0,
    "updated_at": "2026-06-05T00:00:00+00:00",
}


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _stub_complete(
    monkeypatch: pytest.MonkeyPatch, *, capture: dict[str, Any] | None = None
) -> None:
    """generate_once を JSON 文字列を返すスタブに差し替える。capture に渡された messages を残す。"""

    async def _fake_generate(messages, *, source="chat"):  # noqa: ANN001
        if capture is not None:
            capture["messages"] = messages
            capture["source"] = source
        return json.dumps(
            {"summary_md": "# トヨタ\n更新後の要約", "key_facts": {"per": 12.3}},
            ensure_ascii=False,
        )

    monkeypatch.setattr(engine, "generate_once", _fake_generate)


def _stub_fetch_news(
    monkeypatch: pytest.MonkeyPatch,
    articles: list[dict[str, Any]],
    *,
    capture: dict[str, Any] | None = None,
) -> None:
    """fetch_news を固定の記事 list を返すスタブに差し替える（mode/since を capture）。"""

    async def _fake_fetch(code, *, since=None, mode):  # noqa: ANN001
        if capture is not None:
            capture["code"] = code
            capture["since"] = since
            capture["mode"] = mode
        return articles

    monkeypatch.setattr(dossier, "fetch_news", _fake_fetch)


_ARTICLE = {
    "url": "https://example.com/news/1",
    "title": "トヨタ最高益",
    "summary": "通期最高益の見通し",
    "published_at": "2026-06-04",
    "source_type": "news",
    "body": "ここに全文が入る（パイプラインは渡してはならない）",
}


def test_investigate_records_source_and_advances_timestamp(
    monkeypatch: pytest.MonkeyPatch, temp_db: None
) -> None:
    """新着 1 件を台帳に記録し、ドシエ本体の last_investigated_at が前進する。"""
    repo.upsert_stocks([STOCK])
    _stub_complete(monkeypatch)
    _stub_fetch_news(monkeypatch, [_ARTICLE])

    with get_engine().begin() as conn:
        result = _run(dossier.investigate_stock(conn, "7203", mode="nightly"))

    assert result["code"] == "7203"
    assert result["n_sources_added"] == 1
    assert result["summary_md"] == "# トヨタ\n更新後の要約"
    assert result["last_investigated_at"]

    with get_engine().connect() as conn:
        saved = repo.get_dossier(conn, "7203")
        sources = repo.list_dossier_sources(conn, "7203")
    assert saved is not None
    assert saved["last_investigated_at"] == result["last_investigated_at"]
    assert json.loads(saved["key_facts"]) == {"per": 12.3}
    assert len(sources) == 1
    # 本文非保存: 台帳に summary/url はあるが body 列は存在しない（ADR-020）。
    assert sources[0]["url"] == _ARTICLE["url"]
    assert sources[0]["summary"] == _ARTICLE["summary"]
    assert "body" not in sources[0]


def test_investigate_dedupes_existing_url(monkeypatch: pytest.MonkeyPatch, temp_db: None) -> None:
    """既存 url の記事は二重取り込みしない（2 回目は n_sources_added=0・行は増えない）。"""
    repo.upsert_stocks([STOCK])
    _stub_complete(monkeypatch)
    _stub_fetch_news(monkeypatch, [_ARTICLE])

    with get_engine().begin() as conn:
        first = _run(dossier.investigate_stock(conn, "7203", mode="nightly"))
    with get_engine().begin() as conn:
        second = _run(dossier.investigate_stock(conn, "7203", mode="nightly"))

    assert first["n_sources_added"] == 1
    assert second["n_sources_added"] == 0
    with get_engine().connect() as conn:
        sources = repo.list_dossier_sources(conn, "7203")
    assert len(sources) == 1  # 同じ url なので 1 行のまま


def test_investigate_passes_mode_to_fetch_news(
    monkeypatch: pytest.MonkeyPatch, temp_db: None
) -> None:
    """mode と since（today-7d）が fetch_news に正しく渡る（nightly/chat 経路）。"""
    repo.upsert_stocks([STOCK])
    _stub_complete(monkeypatch)

    for mode in ("nightly", "chat"):
        cap: dict[str, Any] = {}
        _stub_fetch_news(monkeypatch, [], capture=cap)
        with get_engine().begin() as conn:
            _run(dossier.investigate_stock(conn, "7203", mode=mode))  # type: ignore[arg-type]
        assert cap["mode"] == mode
        assert cap["code"] == "7203"
        # since は 'YYYY-MM-DD' 形式（発行 1 週間以内の下限）。
        assert cap["since"] and len(cap["since"]) == 10


def test_summarize_receives_only_digests_not_full_text(
    monkeypatch: pytest.MonkeyPatch, temp_db: None
) -> None:
    """summarize_dossier（generate_once）に渡るのは記事の要約と財務事実のみで全文は載らない（ADR-014）。"""
    repo.upsert_stocks([STOCK])
    cap: dict[str, Any] = {}
    _stub_complete(monkeypatch, capture=cap)
    _stub_fetch_news(monkeypatch, [_ARTICLE])

    with get_engine().begin() as conn:
        _run(dossier.investigate_stock(conn, "7203", mode="chat"))

    # complete の user メッセージ（JSON payload）に記事全文が含まれないこと。
    user_msg = next(m for m in cap["messages"] if m["role"] == "user")
    payload = json.loads(user_msg["content"])
    assert payload["new_articles"][0]["summary"] == _ARTICLE["summary"]
    assert "body" not in payload["new_articles"][0]
    assert _ARTICLE["body"] not in user_msg["content"]
    assert cap["source"] == "dossier"


def test_summarize_keeps_existing_on_broken_json(
    monkeypatch: pytest.MonkeyPatch, temp_db: None
) -> None:
    """LLM 応答が JSON でないとき本文を summary に採用し key_facts は既存維持（堅牢化）。"""

    async def _bad_generate(messages, *, source="chat"):  # noqa: ANN001
        return "ただのテキスト応答"

    monkeypatch.setattr(engine, "generate_once", _bad_generate)

    existing = {"summary_md": "古い要約", "key_facts": '{"per": 9.0}'}
    summary_md, key_facts = _run(dossier.summarize_dossier(existing, [], []))
    assert summary_md == "ただのテキスト応答"
    assert key_facts == '{"per": 9.0}'  # 既存維持
