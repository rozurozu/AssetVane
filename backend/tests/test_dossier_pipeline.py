"""Phase 4 ドシエ調査パイプライン（investigate_stock / summarize_dossier）を検証する。

担保すること（phase4-spec §3・§8・ADR-020/014）:
- URL 重複排除: 既存 url の記事は二重取り込みしない（n_sources_added・行数で確認）。
- 本文非保存: パイプラインが news へ渡すのは summary/url 等のみで全文を渡さない（ADR-020/044）。
- last_investigated_at / updated_at が前進する。
- 社名解決: repo.get_stock の company_name が fetch_news に渡る（取れなければ code＝ADR-020 改訂）。
- extraction_status が記事 → news まで届く（ADR-020 改訂）。
- 旧 source_type を news.source へ移し level="stock"＋code でタグ付けする（ADR-044）。
- since（today-7d）が fetch_news に正しく渡る（取得下限）。
- summarize_dossier に渡るのが「記事の要約と財務事実のみ」で、全文を載せない（ADR-014）。
- LLM 要約失敗時は DML 自体が発行されず部分書き込みが残らない（C-6・tasks/review-2026-06-12.md）。

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
    """fetch_news を固定の記事 list を返すスタブに差し替える（code/company_name/since を capture）。

    新シグネチャ `fetch_news(code, company_name, *, since=None)` に合わせる（mode は廃止＝
    ADR-020 改訂）。adapter は DB に触らず社名は呼び出し側が解決する契約なので、
    company_name が渡ってくることを capture で検証できるようにする。
    """

    async def _fake_fetch(code, company_name, *, since=None):  # noqa: ANN001
        if capture is not None:
            capture["code"] = code
            capture["company_name"] = company_name
            capture["since"] = since
        return articles

    monkeypatch.setattr(dossier, "fetch_news", _fake_fetch)


_ARTICLE = {
    "url": "https://example.com/news/1",
    "title": "トヨタ最高益",
    "summary": "通期最高益の見通し",
    "published_at": "2026-06-04",
    "source_type": "news",
    "extraction_status": "summarized",
    "body": "ここに全文が入る（パイプラインは渡してはならない）",
}


def test_investigate_records_source_and_advances_timestamp(
    monkeypatch: pytest.MonkeyPatch, temp_db: None
) -> None:
    """新着 1 件を台帳に記録し last_investigated_at が前進する。extraction_status も届く。"""
    repo.upsert_stocks([STOCK])
    _stub_complete(monkeypatch)
    _stub_fetch_news(monkeypatch, [_ARTICLE])

    with get_engine().begin() as conn:
        result = _run(dossier.investigate_stock(conn, "7203"))

    assert result["code"] == "7203"
    assert result["n_sources_added"] == 1
    assert result["summary_md"] == "# トヨタ\n更新後の要約"
    assert result["last_investigated_at"]

    with get_engine().connect() as conn:
        saved = repo.get_dossier(conn, "7203")
        sources = repo.list_news(conn, level="stock", code="7203")
    assert saved is not None
    assert saved["last_investigated_at"] == result["last_investigated_at"]
    assert json.loads(saved["key_facts"]) == {"per": 12.3}
    assert len(sources) == 1
    # 本文非保存: 統合コーパス news に summary/url はあるが body 列は存在しない（ADR-020/044）。
    assert sources[0]["url"] == _ARTICLE["url"]
    assert sources[0]["summary"] == _ARTICLE["summary"]
    assert "body" not in sources[0]
    # 旧 source_type は news の source へ移し替えられる（ADR-044）。
    assert sources[0]["source"] == _ARTICLE["source_type"]
    # 銘柄ニュースは level="stock"＋code でタグ付けされる（ADR-044）。
    assert sources[0]["level"] == "stock"
    assert sources[0]["code"] == "7203"
    # extraction_status が記事 → news まで届く（ADR-020 改訂・3 段フォールバック記録）。
    assert sources[0]["extraction_status"] == "summarized"


def test_investigate_writes_company_description_for_themes(
    monkeypatch: pytest.MonkeyPatch, temp_db: None
) -> None:
    """ドシエ要約を company_descriptions(JP, source='dossier') に焼く（段階B 信号源・ADR-050）。"""
    repo.upsert_stocks([STOCK])
    _stub_complete(monkeypatch)
    _stub_fetch_news(monkeypatch, [_ARTICLE])

    with get_engine().begin() as conn:
        result = _run(dossier.investigate_stock(conn, "7203"))

    with get_engine().connect() as conn:
        desc = repo.get_company_description(conn, "JP", "7203")
    assert desc is not None
    assert desc["source"] == "dossier"
    assert desc["description_text"] == result["summary_md"]  # summary_md そのまま
    # fetched_at は last_investigated_at と揃う（同一 now・差分タガーのテキスト変化判定の起点）。
    assert desc["fetched_at"] == result["last_investigated_at"]
    assert desc["disclosed_date"] is None  # EDINET 専用 provenance はドシエ由来では None
    assert desc["doc_id"] is None


def test_investigate_company_description_idempotent_on_same_summary(
    monkeypatch: pytest.MonkeyPatch, temp_db: None
) -> None:
    """同一 summary_md の再調査では company_descriptions.fetched_at が据え置かれる（差分の肝）。"""
    repo.upsert_stocks([STOCK])
    _stub_complete(monkeypatch)  # 常に同じ summary_md を返すスタブ
    _stub_fetch_news(monkeypatch, [_ARTICLE])

    with get_engine().begin() as conn:
        _run(dossier.investigate_stock(conn, "7203"))
    with get_engine().connect() as conn:
        first = repo.get_company_description(conn, "JP", "7203")
    with get_engine().begin() as conn:
        _run(dossier.investigate_stock(conn, "7203"))
    with get_engine().connect() as conn:
        second = repo.get_company_description(conn, "JP", "7203")

    # テキスト未変化なので fetched_at は据え置き＝tag_jp_themes が無駄に再タグしない（差分の肝）。
    assert first is not None and second is not None
    assert second["fetched_at"] == first["fetched_at"]


def test_investigate_dedupes_existing_url(monkeypatch: pytest.MonkeyPatch, temp_db: None) -> None:
    """既存 url の記事は二重取り込みしない（2 回目は n_sources_added=0・行は増えない）。"""
    repo.upsert_stocks([STOCK])
    _stub_complete(monkeypatch)
    _stub_fetch_news(monkeypatch, [_ARTICLE])

    with get_engine().begin() as conn:
        first = _run(dossier.investigate_stock(conn, "7203"))
    with get_engine().begin() as conn:
        second = _run(dossier.investigate_stock(conn, "7203"))

    assert first["n_sources_added"] == 1
    assert second["n_sources_added"] == 0
    with get_engine().connect() as conn:
        sources = repo.list_news(conn, level="stock", code="7203")
    assert len(sources) == 1  # 同じ url なので 1 行のまま


def test_investigate_resolves_company_name_and_since(
    monkeypatch: pytest.MonkeyPatch, temp_db: None
) -> None:
    """社名解決（repo.get_stock の company_name）と since が fetch_news に渡る（ADR-020）。"""
    repo.upsert_stocks([STOCK])
    _stub_complete(monkeypatch)

    cap: dict[str, Any] = {}
    _stub_fetch_news(monkeypatch, [], capture=cap)
    with get_engine().begin() as conn:
        _run(dossier.investigate_stock(conn, "7203"))
    assert cap["code"] == "7203"
    # 社名は stocks の company_name から解決される（adapter は DB に触らず呼び出し側が解決）。
    assert cap["company_name"] == "トヨタ自動車"
    # since は 'YYYY-MM-DD' 形式（発行 1 週間以内の下限）。
    assert cap["since"] and len(cap["since"]) == 10


def test_investigate_falls_back_to_code_when_name_missing(
    monkeypatch: pytest.MonkeyPatch, temp_db: None
) -> None:
    """company_name が NULL の銘柄は code を社名代わりに渡す（検索が空振りしても落とさない）。"""
    # stocks 行はあるが company_name は NULL（stock_dossiers の FK は満たす）。
    repo.upsert_stocks([{**STOCK, "company_name": None}])
    _stub_complete(monkeypatch)
    cap: dict[str, Any] = {}
    _stub_fetch_news(monkeypatch, [], capture=cap)
    with get_engine().begin() as conn:
        _run(dossier.investigate_stock(conn, "7203"))
    assert cap["company_name"] == "7203"


def test_summarize_receives_only_digests_not_full_text(
    monkeypatch: pytest.MonkeyPatch, temp_db: None
) -> None:
    """summarize_dossier（generate_once）に渡るのは記事の要約と財務事実のみで全文は載らない（ADR-014）。"""
    repo.upsert_stocks([STOCK])
    cap: dict[str, Any] = {}
    _stub_complete(monkeypatch, capture=cap)
    _stub_fetch_news(monkeypatch, [_ARTICLE])

    with get_engine().begin() as conn:
        _run(dossier.investigate_stock(conn, "7203"))

    # complete の user メッセージ（JSON payload）に記事全文が含まれないこと。
    user_msg = next(m for m in cap["messages"] if m["role"] == "user")
    payload = json.loads(user_msg["content"])
    assert payload["new_articles"][0]["summary"] == _ARTICLE["summary"]
    assert "body" not in payload["new_articles"][0]
    assert _ARTICLE["body"] not in user_msg["content"]
    assert cap["source"] == "dossier"


def test_investigate_no_partial_write_when_summarize_fails(
    monkeypatch: pytest.MonkeyPatch, temp_db: None
) -> None:
    """LLM 要約が例外を投げたら DB へ部分書き込みが残らない（news もドシエも書かれない）。

    tasks/review-2026-06-12.md C-6 の再構成（await を全部終えてから書き込みを束ねる）の担保。
    upsert_news が LLM 失敗時に一度も呼ばれないこと（＝要約前に DML を発行せず SQLite の
    書きロックを取らないこと）と、呼び出し側 begin() のロールバック後に DB が空のままで
    あることの両方を確認する。
    """
    repo.upsert_stocks([STOCK])
    _stub_fetch_news(monkeypatch, [_ARTICLE])

    async def _broken_generate(messages, *, source="chat"):  # noqa: ANN001
        raise RuntimeError("LLM 呼び出し失敗（タイムアウト想定）")

    monkeypatch.setattr(engine, "generate_once", _broken_generate)

    # upsert_news の呼び出しをスパイする（dossier は repo モジュール属性経由で呼ぶ）。
    calls: list[int] = []
    original_upsert_news = repo.upsert_news

    def _spy_upsert_news(conn, rows):  # noqa: ANN001
        calls.append(len(rows))
        return original_upsert_news(conn, rows)

    monkeypatch.setattr(repo, "upsert_news", _spy_upsert_news)

    with pytest.raises(RuntimeError):
        with get_engine().begin() as conn:
            _run(dossier.investigate_stock(conn, "7203"))

    # LLM 失敗時は news の DML 自体が発行されない（C-6: 要約前に書きロックを取らない）。
    assert calls == []
    # ロールバック後、news・ドシエ・company_descriptions のいずれも書かれていない（原子的）。
    with get_engine().connect() as conn:
        assert repo.get_dossier(conn, "7203") is None
        assert repo.list_news(conn, level="stock", code="7203") == []
        assert repo.get_company_description(conn, "JP", "7203") is None


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
