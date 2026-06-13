"""ニュース定性 polarity（ADR-049/051・能動配信の前処理）を検証する。

担保すること:
- parser: _parse_polarity_response が 3 値 enum 正規化（大文字小文字・コードフェンス・壊れ JSON・
  値域外・幻 id・neutral 保持）を通す（LLM 不要の純パース）。
- classify_polarities: generate_once を mock し id→polarity の dict を返す（ネットに出ない）。
- repo: list_news_needing_polarity（stock×NULL のみ・summary 空除外）・update_news_polarity・
  list_negative_stock_news_for_codes（code IN×negative×fetched_at 窓×stock 層・社名 JOIN）。
- job: tag_news_polarity が正常付与／壊れ応答は NULL のまま／LLM 例外で ok=False（C-7 対称）／
  総崩れで ok=False／対象なしで ok=True。

本物の DB に触れず一時 SQLite（temp_db）で回す。LLM は mock（ネットに出ない・testing-strategy）。
"""

from __future__ import annotations

import asyncio

from app.advisor import news_polarity
from app.batch.jobs import tag_news_polarity
from app.db import repo
from app.db.engine import get_engine
from app.db.schema import news, stocks


def _run(coro):  # noqa: ANN001, ANN202 — テスト専用の同期駆動（test_news_embedding と同流儀）
    return asyncio.run(coro)


def _seed_stocks(*codes: str) -> None:
    """stock 層 news は code FK（stocks.code）を持つため、銘柄を先に入れる（FK 充足）。"""
    with get_engine().begin() as conn:
        for code in codes:
            conn.execute(stocks.insert().values(code=code, company_name=f"{code} 社"))


def _insert_news(
    url: str,
    *,
    level: str = "stock",
    code: str | None = "7203",
    summary: str = "要約。",
    title: str = "見出し",
    polarity: str | None = None,
    fetched_at: str = "2026-06-13T00:00:00+00:00",
    published_at: str | None = "2026-06-13",
) -> int:
    """テスト用に news 1 行を直接 INSERT し id を返す。"""
    with get_engine().begin() as conn:
        result = conn.execute(
            news.insert().values(
                level=level,
                code=code,
                source="news",
                url=url,
                title=title,
                summary=summary,
                published_at=published_at,
                fetched_at=fetched_at,
                extraction_status="summarized",
                polarity=polarity,
            )
        )
    return int(result.inserted_primary_key[0])


# ---------------------------------------------------------------------------
# parser（_parse_polarity_response）
# ---------------------------------------------------------------------------


def test_parse_polarity_valid() -> None:
    """正常 JSON から id→polarity の 3 値が入る（neutral も保持・ADR-049）。"""
    content = '{"results": [{"id": 1, "polarity": "positive"}, {"id": 2, "polarity": "neutral"}]}'
    assert news_polarity._parse_polarity_response(content, {1, 2}) == {1: "positive", 2: "neutral"}


def test_parse_polarity_strips_code_fence_and_normalizes_case() -> None:
    """コードフェンス剥がし＋大文字小文字の正規化（'Negative'→'negative'）。"""
    content = '```json\n{"results": [{"id": 3, "polarity": "Negative"}]}\n```'
    assert news_polarity._parse_polarity_response(content, {3}) == {3: "negative"}


def test_parse_polarity_broken_json_returns_empty() -> None:
    """JSON でない応答は空 dict（NULL のまま・ADR-018）。"""
    assert news_polarity._parse_polarity_response("これは JSON ではない", {1}) == {}
    assert news_polarity._parse_polarity_response(None, {1}) == {}


def test_parse_polarity_discards_out_of_enum_and_phantom_id() -> None:
    """値域外の polarity と valid_ids に無い幻 id は破棄する（ADR-049）。"""
    content = (
        '{"results": ['
        '{"id": 1, "polarity": "strong_buy"},'  # 値域外 → 破棄
        '{"id": 99, "polarity": "positive"},'  # 幻 id → 破棄
        '{"id": 2, "polarity": "negative"}]}'  # 正常
    )
    assert news_polarity._parse_polarity_response(content, {1, 2}) == {2: "negative"}


def test_parse_polarity_dedup_first_wins() -> None:
    """同一 id が重複したら先勝ち。"""
    content = '{"results": [{"id": 1, "polarity": "positive"}, {"id": 1, "polarity": "negative"}]}'
    assert news_polarity._parse_polarity_response(content, {1}) == {1: "positive"}


def test_classify_polarities_uses_llm(monkeypatch) -> None:
    """classify_polarities が generate_once を呼び id→polarity を返す（mock・ネット非依存）。"""
    from app.advisor import engine

    async def _fake_generate_once(messages, *, source):  # noqa: ANN001, ANN202
        assert source == "tagger"
        return '{"results": [{"id": 10, "polarity": "negative"}]}'

    monkeypatch.setattr(engine, "generate_once", _fake_generate_once)
    out = _run(news_polarity.classify_polarities([{"id": 10, "title": "t", "summary": "s"}]))
    assert out == {10: "negative"}


def test_classify_polarities_empty_returns_empty() -> None:
    """記事ゼロなら LLM を呼ばず空 dict。"""
    assert _run(news_polarity.classify_polarities([])) == {}


# ---------------------------------------------------------------------------
# repo
# ---------------------------------------------------------------------------


def test_list_news_needing_polarity_filters(temp_db) -> None:
    """stock×polarity NULL のみ返し、他層・判定済み・summary 空を除外する（ADR-049/051）。"""
    _seed_stocks("7203")
    target = _insert_news("https://x/t", level="stock", polarity=None)
    _insert_news("https://x/done", level="stock", polarity="positive")  # 判定済み → 除外
    _insert_news("https://x/empty", level="stock", polarity=None, summary="")  # summary 空 → 除外
    _insert_news("https://x/market", level="market", code=None, polarity=None)  # 他層 → 除外

    with get_engine().connect() as conn:
        rows = repo.list_news_needing_polarity(conn, limit=10)
    assert [r["id"] for r in rows] == [target]


def test_update_news_polarity_writes_value(temp_db) -> None:
    """update_news_polarity が 1 行の polarity を更新し他行を触らない。"""
    _seed_stocks("7203")
    nid = _insert_news("https://x/u", polarity=None)
    other = _insert_news("https://x/o", polarity=None)
    with get_engine().begin() as conn:
        repo.update_news_polarity(conn, nid, "negative")
    with get_engine().connect() as conn:
        rows = {r["id"]: r["polarity"] for r in conn.execute(news.select()).mappings().all()}
    assert rows[nid] == "negative"
    assert rows[other] is None


def test_list_negative_stock_news_for_codes(temp_db) -> None:
    """code IN × negative × fetched_at 窓 × stock 層のみ・社名 JOIN・降順を担保する（ADR-051）。"""
    _seed_stocks("7203", "6758", "9999")
    since = "2026-06-12T00:00:00+00:00"
    hit_new = _insert_news(
        "https://x/hit2", code="7203", polarity="negative", fetched_at="2026-06-13T09:00:00+00:00"
    )
    hit_old = _insert_news(
        "https://x/hit1", code="6758", polarity="negative", fetched_at="2026-06-13T01:00:00+00:00"
    )
    _insert_news(
        "https://x/pos", code="7203", polarity="positive", fetched_at="2026-06-13T05:00:00+00:00"
    )  # positive → 除外
    _insert_news(
        "https://x/old", code="7203", polarity="negative", fetched_at="2026-06-10T00:00:00+00:00"
    )  # 窓外 → 除外
    _insert_news(
        "https://x/other", code="9999", polarity="negative", fetched_at="2026-06-13T05:00:00+00:00"
    )  # 非保有 code → 除外（codes に渡さない）

    with get_engine().connect() as conn:
        rows = repo.list_negative_stock_news_for_codes(conn, ["7203", "6758"], fetched_since=since)
    # fetched_at 降順（新しい順）。
    assert [r["id"] for r in rows] == [hit_new, hit_old]
    assert rows[0]["company_name"] == "7203 社"  # LEFT JOIN で社名補完


def test_list_negative_stock_news_empty_codes_returns_empty(temp_db) -> None:
    """codes 空なら [] を返す（DB に行かない）。"""
    with get_engine().connect() as conn:
        assert repo.list_negative_stock_news_for_codes(conn, [], fetched_since="2026-06-12") == []


# ---------------------------------------------------------------------------
# job（tag_news_polarity）
# ---------------------------------------------------------------------------


def test_tag_news_polarity_no_target(temp_db) -> None:
    """判定対象が無ければ ok=True・rows=0。"""
    result = tag_news_polarity.run()
    assert result.ok is True
    assert result.rows == 0


def test_tag_news_polarity_tags_rows(monkeypatch, temp_db) -> None:
    """全件正常判定で stock 層に polarity が付き ok=True（他層は触らない）。"""
    _seed_stocks("7203", "6758")
    n1 = _insert_news("https://x/a", code="7203", polarity=None)
    n2 = _insert_news("https://x/b", code="6758", polarity=None)
    m1 = _insert_news("https://x/m", level="market", code=None, polarity=None)  # 対象外

    async def _fake_classify(batch):  # noqa: ANN001, ANN202
        return {int(r["id"]): "negative" for r in batch}

    monkeypatch.setattr(tag_news_polarity, "classify_polarities", _fake_classify)
    result = tag_news_polarity.run()

    assert result.ok is True
    assert result.rows == 2
    with get_engine().connect() as conn:
        rows = {r["id"]: r["polarity"] for r in conn.execute(news.select()).mappings().all()}
    assert rows[n1] == "negative"
    assert rows[n2] == "negative"
    assert rows[m1] is None  # market 層は判定しない


def test_tag_news_polarity_broken_response_leaves_null(monkeypatch, temp_db) -> None:
    """一部 id が欠落した応答では、その行は NULL のまま・残りは付く（翌晩再試行）。"""
    _seed_stocks("7203", "6758")
    n1 = _insert_news("https://x/a", code="7203", polarity=None)
    n2 = _insert_news("https://x/b", code="6758", polarity=None)

    async def _fake_partial(batch):  # noqa: ANN001, ANN202 — n1 だけ返し n2 を落とす
        return {n1: "positive"}

    monkeypatch.setattr(tag_news_polarity, "classify_polarities", _fake_partial)
    result = tag_news_polarity.run()

    assert result.ok is True  # enum 外ではなく単なる欠落なので総崩れ扱いにしない
    assert result.rows == 1
    with get_engine().connect() as conn:
        rows = {r["id"]: r["polarity"] for r in conn.execute(news.select()).mappings().all()}
    assert rows[n1] == "positive"
    assert rows[n2] is None  # 欠落分は NULL のまま


def test_tag_news_polarity_llm_exception_not_ok(monkeypatch, temp_db) -> None:
    """LLM 例外なら ok=False（embed_news と契約対称・ADR-018・C-7）。"""
    _seed_stocks("7203")
    _insert_news("https://x/a", code="7203", polarity=None)

    async def _boom(batch):  # noqa: ANN001, ANN202
        raise RuntimeError("LLM down")

    monkeypatch.setattr(tag_news_polarity, "classify_polarities", _boom)
    result = tag_news_polarity.run()

    assert result.ok is False
    assert result.rows == 0


def test_tag_news_polarity_total_enum_out_not_ok(monkeypatch, temp_db) -> None:
    """応答は返るが全件 enum 外（results 空）なら ok=False・NULL のまま（総崩れ・ADR-018）。"""
    _seed_stocks("7203")
    n1 = _insert_news("https://x/a", code="7203", polarity=None)

    async def _empty(batch):  # noqa: ANN001, ANN202
        return {}

    monkeypatch.setattr(tag_news_polarity, "classify_polarities", _empty)
    result = tag_news_polarity.run()

    assert result.ok is False
    assert result.rows == 0
    with get_engine().connect() as conn:
        row = conn.execute(news.select().where(news.c.id == n1)).mappings().first()
    assert row["polarity"] is None


def test_tag_news_polarity_partial_batch_persists_then_not_ok(monkeypatch, temp_db) -> None:
    """1 バッチ目成功→2 バッチ目 LLM 例外でも成功分は永続し ok=False（自己回復・C-7）。"""
    _seed_stocks("7203", "6758")
    n1 = _insert_news("https://x/a", code="7203", polarity=None)
    _insert_news("https://x/b", code="6758", polarity=None)

    monkeypatch.setattr(tag_news_polarity, "POLARITY_BATCH", 1)  # 2 バッチに分割
    calls = {"n": 0}

    async def _fail_on_second(batch):  # noqa: ANN001, ANN202
        calls["n"] += 1
        if calls["n"] >= 2:
            raise RuntimeError("LLM down")
        return {int(r["id"]): "negative" for r in batch}

    monkeypatch.setattr(tag_news_polarity, "classify_polarities", _fail_on_second)
    result = tag_news_polarity.run()

    assert result.ok is False
    assert result.rows == 1  # 1 バッチ目の成功分は数えられる
    with get_engine().connect() as conn:
        tagged = [
            r for r in conn.execute(news.select()).mappings().all() if r["polarity"] is not None
        ]
    assert len(tagged) == 1  # 成功済みは rollback されず永続
    assert tagged[0]["id"] == n1
