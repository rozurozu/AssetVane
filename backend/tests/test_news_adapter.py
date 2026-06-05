"""NewsAdapter（fetch_news / Google News フェッチャ）の単体テスト（ネット非依存）。

testing-strategy: 外部 API は叩かず、サンプル RSS XML・記事 HTML を fixture にして httpx
（RSS GET・記事 GET・batchexecute POST）と generate_once を monkeypatch する。
担保すること（ADR-010/020・plans の完了条件）:
  - 3 段フォールバック（summarized / description / headline）で extraction_status が正しく付く
  - _decode_google_url 失敗でも落ちず Google URL のまま継続する
  - since フィルタ・news_max_articles_per_stock キャップ
  - news_enabled=False で空配列
  - batchexecute 応答・記事 ID/decode パラメータのパース（実 Google には出ないユニット検証）
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.adapters import news
from app.config import settings


def _run(coro: Any) -> Any:
    """async ヘルパを同期テストから駆動する（test_dossier_pipeline.py と同流儀）。"""
    return asyncio.run(coro)


# --- fixtures（サンプル RSS / 記事 HTML） ---------------------------------------

_SAMPLE_RSS = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>トヨタ - Google ニュース</title>
    <item>
      <title>トヨタが新型EVを発表 - 日経</title>
      <link>https://news.google.com/rss/articles/AAAA111?oc=5</link>
      <pubDate>Wed, 04 Jun 2026 09:00:00 GMT</pubDate>
      <source url="https://www.nikkei.com">日経</source>
    </item>
    <item>
      <title>トヨタ決算は増益 - ロイター</title>
      <link>https://news.google.com/rss/articles/BBBB222?oc=5</link>
      <pubDate>Mon, 01 Jun 2026 09:00:00 GMT</pubDate>
      <source url="https://jp.reuters.com">ロイター</source>
    </item>
    <item>
      <title>古いトヨタ記事 - 旧聞</title>
      <link>https://news.google.com/rss/articles/CCCC333?oc=5</link>
      <pubDate>Fri, 01 May 2026 09:00:00 GMT</pubDate>
      <source url="https://example.com">旧聞</source>
    </item>
  </channel>
</rss>
"""

# 本文が十分にある記事 HTML（trafilatura.extract が本文を返せる長さ）。
_ARTICLE_HTML_FULL = (
    "<html><head><title>記事</title>"
    '<meta property="og:description" content="og の説明文">'
    "</head><body><article><p>"
    + ("トヨタは新型の電気自動車を発表した。航続距離は大幅に伸び、価格は据え置きとなる。" * 12)
    + "</p></article></body></html>"
)

# 本文は薄いが og:description は取れる記事 HTML。
_ARTICLE_HTML_OG_ONLY = (
    "<html><head><title>薄い記事</title>"
    '<meta property="og:description" content="これは og の説明文です。本文は取れない。">'
    "</head><body><p>短い</p></body></html>"
)

# 本文も description も無い空の記事 HTML。
_ARTICLE_HTML_EMPTY = "<html><head><title>空</title></head><body></body></html>"


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code


class _FakeAsyncClient:
    """httpx.AsyncClient のスタブ。URL/パスに応じて固定レスポンスを返す。"""

    def __init__(self, *args: Any, routes: dict[str, str] | None = None, **kwargs: Any) -> None:
        self._routes = routes or {}

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def get(self, url: str) -> _FakeResponse:
        for key, text in self._routes.items():
            if key in url:
                return _FakeResponse(text)
        return _FakeResponse("", status_code=404)

    async def post(self, path: str, **kwargs: Any) -> _FakeResponse:
        return _FakeResponse(self._routes.get("__post__", ""))


@pytest.fixture(autouse=True)
def _enable_news(monkeypatch: pytest.MonkeyPatch) -> None:
    """テスト中は news を有効化し、throttle を無効化（待たない）。"""
    monkeypatch.setattr(settings, "news_enabled", True)
    monkeypatch.setattr(settings, "news_min_interval_seconds", 0.0)
    monkeypatch.setattr(settings, "news_max_articles_per_stock", 10)

    async def _no_throttle() -> None:
        return None

    monkeypatch.setattr(news, "_throttle", _no_throttle)


# --- news_enabled=False ---------------------------------------------------------


def test_fetch_news_disabled_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """news_enabled=False なら即 [] を返す。"""
    monkeypatch.setattr(settings, "news_enabled", False)
    result = _run(news.fetch_news("7203", "トヨタ自動車"))
    assert result == []


# --- 3 段フォールバック ----------------------------------------------------------


def _patch_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    *,
    article_html: str,
    summary: str = "AI 要約された 2 行。",
    decode_url: str = "https://www.nikkei.com/article/real",
) -> None:
    """RSS・URL 復元・本文取得・要約を monkeypatch して net に出ないようにする。"""

    async def _items(company_name: str) -> list[dict]:
        from xml.etree import ElementTree

        root = ElementTree.fromstring(_SAMPLE_RSS)
        out: list[dict] = []
        for item in root.iter("item"):
            out.append(
                {
                    "title": (item.findtext("title") or "").strip(),
                    "link": (item.findtext("link") or "").strip(),
                    "published_at": news._parse_pubdate(item.findtext("pubDate")),
                    "source": None,
                }
            )
        return out

    async def _decode(url: str) -> str:
        return decode_url

    async def _html(url: str) -> str:
        return article_html

    async def _summarize(text: str) -> str:
        return summary

    monkeypatch.setattr(news, "_fetch_rss_items", _items)
    monkeypatch.setattr(news, "_decode_google_url", _decode)
    monkeypatch.setattr(news, "_fetch_html", _html)
    monkeypatch.setattr(news, "_summarize_article", _summarize)


def test_fallback_summarized(monkeypatch: pytest.MonkeyPatch) -> None:
    """本文が十分なら AI 要約され extraction_status='summarized'。"""
    _patch_pipeline(monkeypatch, article_html=_ARTICLE_HTML_FULL)
    result = _run(news.fetch_news("7203", "トヨタ"))
    assert result
    assert all(a["source_type"] == "news" for a in result)
    assert result[0]["extraction_status"] == "summarized"
    assert result[0]["summary"] == "AI 要約された 2 行。"
    assert result[0]["url"] == "https://www.nikkei.com/article/real"
    assert "title" in result[0] and "published_at" in result[0]


def test_fallback_description(monkeypatch: pytest.MonkeyPatch) -> None:
    """本文不足だが og:description があれば summary に採用し 'description'。"""
    _patch_pipeline(monkeypatch, article_html=_ARTICLE_HTML_OG_ONLY)
    result = _run(news.fetch_news("7203", "トヨタ"))
    assert result[0]["extraction_status"] == "description"
    assert result[0]["summary"] == "これは og の説明文です。本文は取れない。"


def test_fallback_headline(monkeypatch: pytest.MonkeyPatch) -> None:
    """本文も description も無ければ見出しのみ・summary=None で 'headline'。"""
    _patch_pipeline(monkeypatch, article_html=_ARTICLE_HTML_EMPTY)
    result = _run(news.fetch_news("7203", "トヨタ"))
    assert result[0]["extraction_status"] == "headline"
    assert result[0]["summary"] is None
    assert result[0]["title"]


def test_summarize_failure_falls_back_to_description(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """本文はあるが要約が例外 → description へフォールバック（落ちない）。"""
    _patch_pipeline(monkeypatch, article_html=_ARTICLE_HTML_FULL)

    async def _boom(text: str) -> str:
        raise RuntimeError("LLM 障害")

    monkeypatch.setattr(news, "_summarize_article", _boom)
    result = _run(news.fetch_news("7203", "トヨタ"))
    # og:description が _ARTICLE_HTML_FULL にもあるため description に落ちる
    assert result[0]["extraction_status"] == "description"


def test_decode_failure_keeps_google_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """URL 復元失敗（Google URL を返す実装）でも落ちず Google URL のまま継続する。"""
    _patch_pipeline(
        monkeypatch,
        article_html=_ARTICLE_HTML_FULL,
        decode_url="https://news.google.com/rss/articles/AAAA111?oc=5",
    )
    result = _run(news.fetch_news("7203", "トヨタ"))
    assert result
    assert result[0]["url"].startswith("https://news.google.com/rss/articles/")


def test_html_fetch_failure_falls_back_to_headline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """記事本文 GET が例外 → 見出しのみ（headline）に握ってフォールバック。"""
    _patch_pipeline(monkeypatch, article_html=_ARTICLE_HTML_FULL)

    import httpx

    async def _boom(url: str) -> str:
        raise httpx.ConnectError("接続失敗")

    monkeypatch.setattr(news, "_fetch_html", _boom)
    result = _run(news.fetch_news("7203", "トヨタ"))
    assert all(a["extraction_status"] == "headline" for a in result)


# --- since フィルタ・キャップ ----------------------------------------------------


def test_since_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    """since 以降の発行日のみ残る（古い 2026-05-01 の記事は落ちる）。"""
    _patch_pipeline(monkeypatch, article_html=_ARTICLE_HTML_EMPTY)
    result = _run(news.fetch_news("7203", "トヨタ", since="2026-06-01"))
    # RSS の 3 件中、6/4 と 6/1 の 2 件が残る（5/1 は落ちる）
    assert len(result) == 2
    assert all(a["published_at"] >= "2026-06-01" for a in result)


def test_max_articles_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """news_max_articles_per_stock で新しい順に上限キャップされる。"""
    monkeypatch.setattr(settings, "news_max_articles_per_stock", 1)
    _patch_pipeline(monkeypatch, article_html=_ARTICLE_HTML_EMPTY)
    result = _run(news.fetch_news("7203", "トヨタ"))
    assert len(result) == 1
    # 最も新しい 2026-06-04 が残る
    assert result[0]["published_at"] == "2026-06-04"


# --- RSS 解析（_fetch_rss_items を fake AsyncClient で駆動） ---------------------


def test_fetch_rss_items_parses_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """RSS の title/link/pubDate を解析し published_at を 'YYYY-MM-DD' に正規化する。"""
    import httpx

    def _factory(*args: Any, **kwargs: Any) -> _FakeAsyncClient:
        return _FakeAsyncClient(routes={"/rss/search": _SAMPLE_RSS})

    monkeypatch.setattr(httpx, "AsyncClient", _factory)
    items = _run(news._fetch_rss_items("トヨタ"))
    assert len(items) == 3
    assert items[0]["title"] == "トヨタが新型EVを発表 - 日経"
    assert items[0]["link"].startswith("https://news.google.com/rss/articles/")
    assert items[0]["published_at"] == "2026-06-04"


def test_fetch_rss_items_rejects_doctype(monkeypatch: pytest.MonkeyPatch) -> None:
    """DOCTYPE/ENTITY を含む RSS は XXE/billion-laughs 防御で NewsAdapterError。"""
    import httpx

    evil = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><rss></rss>'

    def _factory(*args: Any, **kwargs: Any) -> _FakeAsyncClient:
        return _FakeAsyncClient(routes={"/rss/search": evil})

    monkeypatch.setattr(httpx, "AsyncClient", _factory)
    with pytest.raises(news.NewsAdapterError):
        _run(news._fetch_rss_items("トヨタ"))


# --- パース部のユニット検証（実 Google には出ない部分） --------------------------


def test_extract_article_id() -> None:
    """rss/articles/<id> の id（base64 部）をクエリを落として取り出す。"""
    url = "https://news.google.com/rss/articles/ABC_def-123?oc=5&hl=ja"
    assert news._extract_article_id(url) == "ABC_def-123"
    assert news._extract_article_id("https://www.nikkei.com/article/x") is None


def test_extract_decode_params() -> None:
    """記事ページ HTML から signature(data-n-a-id)・timestamp(data-n-a-ts) を抜く。"""
    html = '<c-wiz data-n-a-id="SIG123" data-n-a-ts="1700000000">x</c-wiz>'
    sig, ts = news._extract_decode_params(html)
    assert sig == "SIG123"
    assert ts == "1700000000"
    # 無ければ (None, None)
    assert news._extract_decode_params("<div></div>") == (None, None)


def test_parse_batchexecute_response_extracts_real_url() -> None:
    """batchexecute 応答（)]}' 付き）から Fbv4je の実 URL を取り出す。"""
    inner = json.dumps(["garturlres", "https://www.nikkei.com/real/article"])
    # チャンクは [["wrb.fr","Fbv4je",<data_json>,...], ...] の形（実応答の 1 チャンク）。
    chunk = [["wrb.fr", "Fbv4je", inner, None, None, None, "generic"]]
    # 1) )]}' 接頭辞のみ
    text = ")]}'\n" + json.dumps(chunk)
    assert news._parse_batchexecute_response(text) == "https://www.nikkei.com/real/article"
    # 2) 接頭辞＋行長プレフィックス付き（実応答は各チャンク前に行長の数値が入る）
    body = ")]}'\n\n123\n" + json.dumps(chunk)
    assert news._parse_batchexecute_response(body) == "https://www.nikkei.com/real/article"


def test_parse_batchexecute_response_unparseable_returns_none() -> None:
    """応答が壊れていれば None（呼び出し側が Google URL のまま続行）。"""
    assert news._parse_batchexecute_response(")]}'\nnot-json") is None
    assert news._parse_batchexecute_response("") is None


def test_parse_pubdate() -> None:
    """RFC822 の pubDate を 'YYYY-MM-DD' に。解析不能・None は None。"""
    assert news._parse_pubdate("Wed, 04 Jun 2026 09:00:00 GMT") == "2026-06-04"
    assert news._parse_pubdate(None) is None
    assert news._parse_pubdate("garbage") is None
