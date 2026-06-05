"""ニュース取得アダプタ（NewsAdapter）— Phase 4 ドシエ用の実ニュース源（httpx 一本）。

設計の真実: docs/phase-specs/phase4-spec.md §3・§4・§7／ADR-010・ADR-020。
グリル合意（todo-plan-linked-brook）で確定したアーキテクチャ:

ADR-010: 外部データソースはアダプタ越しに使う（router/service/batch から直結しない）。
        URL・言語・国・タイムアウト等はすべて config（settings）から読み、ハードコードしない。
ADR-020: ドシエは「取得 → 要約 → 本文は捨てる」。ここで取得した記事は summary と url のみ
        ドシエ台帳（dossier_sources）へ残し、本文（全文）は返さない／保存しない。
        昼=MCP／夜=httpx の 2 系統は撤回し、**httpx 一本**にした（本文抽出は httpx＋trafilatura
        で足りるため）。MCP は将来 403/JS 必須サイト・Google URL 復元の代替候補として残す。

取得フロー（Google News フェッチャ）:
  1. Google News RSS（`/rss/search?q=<社名>&hl=ja&gl=JP&ceid=JP:ja`）を httpx GET。
  2. stdlib（xml.etree）で RSS を解析し、各 item（title/link/pubDate/source）を取り出す。
     `since` で発行日下限フィルタ、`news_max_articles_per_stock` で新しい順に上限キャップ。
  3. 各記事を控えめな並列（Semaphore）で処理:
     - `_decode_google_url`: rss/articles のエンコード URL を batchexecute で実媒体 URL へ復元。
       失敗時は例外を握って Google URL のまま続行（落とさない）。
     - 実 URL を httpx GET（follow_redirects）→ HTML。
     - trafilatura.extract で本文 → 本文十分なら AI 要約 → extraction_status="summarized"。
     - 本文不足 → extract_metadata の description を summary に → "description"。
     - どちらも無し → 見出しのみ・summary=None → "headline"。
     - 記事個別の取得/抽出失敗も握って "headline" にフォールバック（1 件の失敗で全体を落とさない）。

返却契約（呼び出し側＝investigate_stock と配線する境界）:
  各 article dict = {url, title, summary, published_at, source_type:"news", extraction_status}。
  extraction_status は "summarized"|"description"|"headline" の 3 値。本文は返さない（ADR-020）。
  社名は呼び出し側が解決して渡す（adapter は DB に触らない・ADR-010）。code はログ/識別用。
"""

from __future__ import annotations

import asyncio
import json
import logging
from email.utils import parsedate_to_datetime
from urllib.parse import quote
from xml.etree import ElementTree

import httpx
from trafilatura import extract, extract_metadata

from app.config import settings

logger = logging.getLogger(__name__)

# --- 定数（フォールバック既定。実値は settings から読む） ------------------------
_MAX_RETRIES = 3
_RETRY_BASE_SLEEP = 2.0  # 秒。429/一時失敗時の指数バックオフ基数（base × 2^attempt）
_DECODE_CONCURRENCY = 4  # 記事ごと処理の同時実行数（控えめ・Google への負荷とコスト制御）
# 本文として「十分」とみなす最小文字数。これ未満は本文取得失敗扱い（description へフォールバック）。
_MIN_ARTICLE_TEXT_LEN = 200

# Google News の RSS link は実媒体ではなくエンコード URL。実 URL 復元に使う内部エンドポイント。
_BATCHEXECUTE_PATH = "/_/DotsSplashUi/data/batchexecute"
_BATCHEXECUTE_RPC_ID = "Fbv4je"  # garturlreq（Google News URL → 実 URL 復元）の RPC 名

# 記事ごとの要約指示（advisor の CORE/POLICY を使わず最小指示・summarize_dossier と同じ流儀）。
_ARTICLE_SUMMARIZE_INSTRUCTION = (
    "あなたはニュース記事を 2〜3 行で要約する担当である。"
    "渡された記事本文に書かれている事実のみを日本語で簡潔に要約せよ。"
    "推測・憶測・本文に無い情報の補完はしない。前後に地の文や見出しを付けない。"
)


class NewsAdapterError(RuntimeError):
    """NewsAdapter の取得エラー（RSS 取得そのものの失敗等＝ハード失敗）。

    記事個別の取得/抽出/URL 復元の失敗はここでは投げず握ってフォールバックする。RSS 取得の
    ような「源そのものに届かない」失敗だけをこの独自例外で送出し、呼び出し側
    （investigate_stock）が Tool ループを落とさないよう握って扱う（ADR-018・spec §4）。
    """


# ---------------------------------------------------------------------------
# 公開境界: fetch_news（マルチフェッチャ構成）
# ---------------------------------------------------------------------------
async def fetch_news(
    code: str,
    company_name: str,
    *,
    since: str | None = None,
) -> list[dict]:
    """指定銘柄のニュース記事を取得して返す（spec §3・§4 の返却スキーマが正本・ADR-010/020）。

    Args:
        code: 銘柄コード（ログ/識別用。adapter は DB に触らない・ADR-010）。
        company_name: 検索クエリに使う社名（呼び出し側が repo で解決して渡す）。
        since: 取得下限日 'YYYY-MM-DD'（発行日でフィルタ・spec §3）。None なら無制限。

    Returns:
        記事 dict の list。各 article は
        `{url, title, summary, published_at, source_type:"news", extraction_status}`。
        extraction_status は "summarized"|"description"|"headline"。本文は返さない（ADR-020）。
        `news_enabled=False` なら即 `[]`。

    Note:
        現状は Google News フェッチャ 1 個。将来 `_fetch_yahoo_finance` / `_fetch_news_api` を
        足し、ここで結果をマージ＋URL 重複排除する（下の差込口コメント参照）。
    """
    if not settings.news_enabled:
        logger.debug("fetch_news: news_enabled=False のためスキップ（code=%s）", code)
        return []

    articles = await _fetch_google_news(company_name, since=since)

    # --- 将来のマルチフェッチャ差込口（今回は Google のみ） -----------------------
    # 源ごとに前提（RSS 構造・URL 形式・JS 要否）が違うため、共通 RSS 抽象ではなく源別の
    # fetch 関数に分ける。源を足すときはここで gather してマージし、url で重複排除する:
    #   results = await asyncio.gather(
    #       _fetch_google_news(company_name, since=since),
    #       _fetch_yahoo_finance(company_name, since=since),
    #       _fetch_news_api(company_name, since=since),
    #   )
    #   articles = _dedupe_by_url(chain.from_iterable(results))
    # ---------------------------------------------------------------------------
    return articles


# ---------------------------------------------------------------------------
# Google News フェッチャ
# ---------------------------------------------------------------------------
async def _fetch_google_news(company_name: str, *, since: str | None) -> list[dict]:
    """Google News RSS 検索から記事を取得し、3 段フォールバックで要約まで付けて返す。

    RSS 取得そのものの失敗は NewsAdapterError（ハード失敗）。記事個別の失敗は握って継続する。
    """
    items = await _fetch_rss_items(company_name)

    # 発行日下限フィルタ（since 'YYYY-MM-DD'）＋新しい順に上限キャップ（コスト制御・spec §3）。
    if since is not None:
        items = [it for it in items if it["published_at"] is None or it["published_at"] >= since]
    items.sort(key=lambda it: it["published_at"] or "", reverse=True)
    items = items[: settings.news_max_articles_per_stock]

    # 記事ごとに本文取得 → 要約（控えめな並列・1 記事の失敗で全体を落とさない）。
    semaphore = asyncio.Semaphore(_DECODE_CONCURRENCY)

    async def _bounded(item: dict) -> dict:
        async with semaphore:
            return await _process_item(item)

    return list(await asyncio.gather(*(_bounded(it) for it in items)))


async def _fetch_rss_items(company_name: str) -> list[dict]:
    """Google News RSS を GET し、各 item を素の dict に解析する（title/link/pubDate/source）。

    RSS 取得・パースの失敗は NewsAdapterError（源に届かないハード失敗・呼び出し側が握る）。
    """
    # lang/country は config から組む（ハードコード禁止・ADR-010）。
    lang = settings.google_news_lang
    country = settings.google_news_country
    path = f"/rss/search?q={quote(company_name)}&hl={lang}&gl={country}&ceid={country}:{lang}"
    async with httpx.AsyncClient(
        base_url=settings.google_news_base_url,
        timeout=settings.news_http_timeout_seconds,
        follow_redirects=True,
    ) as client:
        try:
            text = await _get_with_retry(client, path)
        except (httpx.HTTPError, NewsAdapterError) as exc:
            raise NewsAdapterError(
                f"Google News RSS の取得に失敗しました（q={company_name}）: {exc}"
            ) from exc

    # XML セキュリティ: 外部実体（XXE）・実体展開（billion laughs）を避ける。stdlib の
    # ElementTree は外部実体を解決しないが、内部実体宣言を含む DOCTYPE は弾いて多重展開も防ぐ
    # （source は Google News という第一者の信頼できる RSS だが、防御的に保つ）。
    if "<!DOCTYPE" in text or "<!ENTITY" in text:
        raise NewsAdapterError(
            f"Google News RSS に DOCTYPE/ENTITY 宣言が含まれます（q={company_name}）: 拒否します。"
        )
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        raise NewsAdapterError(
            f"Google News RSS の解析に失敗しました（q={company_name}）: {exc}"
        ) from exc

    items: list[dict] = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not link:
            continue
        source = item.findtext("source")
        items.append(
            {
                "title": title,
                "link": link,  # Google のエンコード URL（後で実 URL へ復元を試みる）
                "published_at": _parse_pubdate(item.findtext("pubDate")),
                "source": (source or "").strip() or None,
            }
        )
    return items


async def _process_item(item: dict) -> dict:
    """1 記事を本文取得 → 3 段フォールバックで要約まで付けて article dict にする。

    どの段でも見出し（title）はドシエ合成で使うため無駄にならない。記事個別の GET/抽出/復元の
    失敗はすべて握って "headline" にフォールバックする（1 記事の失敗で全体を落とさない）。
    """
    google_url = item["link"]
    title = item["title"]
    published_at = item["published_at"]

    def _article(url: str, summary: str | None, status: str) -> dict:
        # dedup/保存キーの url は復元成功時は実媒体 URL、失敗時は Google URL
        # （呼び出し側の dossier_sources.url UNIQUE を維持・ADR-020）。
        return {
            "url": url,
            "title": title,
            "summary": summary,
            "published_at": published_at,
            "source_type": "news",
            "extraction_status": status,
        }

    # 実 URL 復元（失敗時は Google URL のまま続行）。
    real_url = await _decode_google_url(google_url)

    try:
        html = await _fetch_html(real_url)
    except httpx.HTTPError as exc:
        logger.debug("記事本文の取得に失敗（%s）: %s → 見出しのみ", real_url, exc)
        return _article(real_url, None, "headline")

    # 1 段目: 本文が十分に取れた → AI 要約。
    body = extract(html)
    if body and len(body) >= _MIN_ARTICLE_TEXT_LEN:
        try:
            summary = await _summarize_article(body)
            return _article(real_url, summary, "summarized")
        except Exception as exc:  # noqa: BLE001 — 要約失敗で記事を捨てず description/headline へ落とす
            logger.warning("記事要約に失敗（%s）: %s → description/headline へ", real_url, exc)

    # 2 段目: 本文不足だが og/meta description が取れた → それを summary に。
    description = _extract_description(html)
    if description:
        return _article(real_url, description, "description")

    # 3 段目: どちらも無し → 見出しのみ。
    return _article(real_url, None, "headline")


async def _fetch_html(url: str) -> str:
    """実媒体 URL を GET して HTML 文字列を返す（リダイレクト追従）。"""
    async with httpx.AsyncClient(
        timeout=settings.news_http_timeout_seconds,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (compatible; AssetVane/1.0)"},
    ) as client:
        resp = await _get_with_retry(client, url)
        return resp


def _extract_description(html: str) -> str | None:
    """trafilatura.extract_metadata の description を取り出す（本文不足時のフォールバック）。"""
    try:
        meta = extract_metadata(html)
    except Exception as exc:  # noqa: BLE001 — メタ抽出失敗は description なし扱いで握る
        logger.debug("extract_metadata に失敗: %s", exc)
        return None
    if meta is None:
        return None
    description = getattr(meta, "description", None)
    return description.strip() if isinstance(description, str) and description.strip() else None


async def _summarize_article(text: str) -> str:
    """記事本文を LLM 単発で 2〜3 行に要約する（summarize_dossier と同じ流儀・ADR-014/020）。

    advisor の CORE/POLICY は使わず、最小指示＋本文を渡して `generate_once(source="dossier")` を
    1 回呼ぶ。engine は import 鎖の先（service→registry→…）にあるため遅延 import で循環を断つ。
    """
    from app.advisor.engine import generate_once

    messages: list[dict[str, object]] = [
        {"role": "system", "content": _ARTICLE_SUMMARIZE_INSTRUCTION},
        {"role": "user", "content": text},
    ]
    return await generate_once(messages, source="dossier")


# ---------------------------------------------------------------------------
# Google News URL 復元（batchexecute）
# ---------------------------------------------------------------------------
async def _decode_google_url(google_url: str) -> str:
    """Google News の rss/articles エンコード URL を実媒体 URL へ復元する。

    方式（参考: gist huksley/bc3cb046… ・n8n テンプレ）:
      1. 記事ページ（rss/articles/<base64id>）を GET し、HTML 中の <c-wiz> から
         パラメータ（signature=data-n-a-id・timestamp=data-n-a-ts・id=URL の base64 部）を抜く。
      2. batchexecute（Fbv4je=garturlreq）へ POST し、応答（`)]}'` 付き配列）から実 URL を得る。
    失敗時は例外を握って **元の Google URL を返す**（落とさない・以降は Google URL のまま続行）。
    """
    try:
        # rss/articles/<id>?... の <id>（base64 部）を取り出す。
        article_id = _extract_article_id(google_url)
        if article_id is None:
            return google_url

        async with httpx.AsyncClient(
            base_url=settings.google_news_base_url,
            timeout=settings.news_http_timeout_seconds,
            follow_redirects=True,
        ) as client:
            page = await _get_with_retry(client, f"/rss/articles/{article_id}")
            signature, timestamp = _extract_decode_params(page)
            if signature is None or timestamp is None:
                return google_url

            real_url = await _post_batchexecute(client, article_id, signature, timestamp)
            return real_url or google_url
    except Exception as exc:  # noqa: BLE001 — 復元は best-effort。失敗は握って Google URL を返す
        logger.debug("Google URL 復元に失敗（%s）: %s → Google URL のまま続行", google_url, exc)
        return google_url


def _extract_article_id(google_url: str) -> str | None:
    """Google News URL から rss/articles/<id> の <id>（base64 部）を取り出す。"""
    marker = "/rss/articles/"
    idx = google_url.find(marker)
    if idx < 0:
        return None
    tail = google_url[idx + len(marker) :]
    # クエリ（?oc=5 等）を落とす。
    return tail.split("?", 1)[0].split("/", 1)[0] or None


def _extract_decode_params(page_html: str) -> tuple[str | None, str | None]:
    """記事ページ HTML の <c-wiz> から signature(data-n-a-id) と timestamp(data-n-a-ts) を抜く。

    Google 仕様変更に弱いので、見つからなければ (None, None) を返し、呼び出し側が Google URL の
    まま続行する。正規表現で属性を拾う（ブラウザ不要・stdlib のみ）。
    """
    import re

    sig_match = re.search(r'data-n-a-id="([^"]+)"', page_html)
    ts_match = re.search(r'data-n-a-ts="([^"]+)"', page_html)
    signature = sig_match.group(1) if sig_match else None
    timestamp = ts_match.group(1) if ts_match else None
    return signature, timestamp


async def _post_batchexecute(
    client: httpx.AsyncClient,
    article_id: str,
    signature: str,
    timestamp: str,
) -> str | None:
    """batchexecute（Fbv4je=garturlreq）へ POST し、応答から実媒体 URL を取り出す。

    f.req は `[[[rpc_id, inner_json, null, "generic"]]]`。inner_json は
    `["garturlreq",[[...],...],"<id>","<signature>",<timestamp>]`（gist huksley/… 準拠）。
    応答は `)]}'` 接頭辞付きの JSON 配列で、ネストした要素のどこかに実 URL が入る。
    """
    # garturlreq のプレースホルダ配列（gist huksley/… の固定形。中身は Google 側が無視する "X"）。
    placeholder = [
        [
            "X",
            "X",
            ["X", "X"],
            None,
            None,
            1,
            1,
            "US:en",
            None,
            1,
            None,
            None,
            None,
            None,
            None,
            0,
            1,
        ],
        "X",
        "X",
        1,
        [1, 1, 1],
        1,
        1,
        None,
        0,
        0,
        None,
        0,
    ]
    inner = json.dumps(
        ["garturlreq", placeholder, article_id, int(timestamp), signature],
        ensure_ascii=False,
    )
    f_req = json.dumps([[[_BATCHEXECUTE_RPC_ID, inner, None, "generic"]]], ensure_ascii=False)

    resp = await client.post(
        _BATCHEXECUTE_PATH,
        params={
            "rpcids": _BATCHEXECUTE_RPC_ID,
            "source-path": "/rss/articles/",
            "hl": settings.google_news_lang,
        },
        data={"f.req": f_req},
        headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
    )
    if resp.status_code >= 400:
        return None
    return _parse_batchexecute_response(resp.text)


def _parse_batchexecute_response(text: str) -> str | None:
    """batchexecute 応答（`)]}'` 付き JSON 配列）から実媒体 URL を取り出す。

    応答は `)]}'\n<行長>\n[[...]]` の形。Fbv4je の行を見つけ、その data ペイロード
    （JSON 文字列）をさらにパースして 2 要素目を実 URL として取り出す（gist 準拠）。
    解析できなければ None（呼び出し側が Google URL のまま続行）。
    """
    # 接頭辞 `)]}'` を落とし、各「行長＋JSON」チャンクを順に走査する。
    body = text
    if body.startswith(")]}'"):
        body = body[len(")]}'") :]
    body = body.lstrip("\n")

    # チャンクを 1 つずつ JSON デコードして Fbv4je 応答を探す。
    decoder = json.JSONDecoder()
    pos = 0
    length = len(body)
    while pos < length:
        # 行頭の数値（チャンク長）はスキップする。
        while pos < length and (body[pos].isdigit() or body[pos] in "\r\n"):
            pos += 1
        if pos >= length:
            break
        try:
            chunk, end = decoder.raw_decode(body, pos)
        except json.JSONDecodeError:
            break
        pos = end
        url = _extract_url_from_chunk(chunk)
        if url:
            return url
    return None


def _extract_url_from_chunk(chunk: object) -> str | None:
    """batchexecute の 1 チャンクから Fbv4je（garturlres）の実 URL を取り出す。

    チャンクは `[["wrb.fr","Fbv4je","<data_json>", ...]]` の形。data_json をさらに JSON
    パースすると `["garturlres","<real_url>",...]` になる（gist 準拠）。構造差に弱いので
    取り出せなければ None。
    """
    if not isinstance(chunk, list):
        return None
    for row in chunk:
        if not isinstance(row, list) or len(row) < 3:
            continue
        if row[1] != _BATCHEXECUTE_RPC_ID:
            continue
        data_json = row[2]
        if not isinstance(data_json, str):
            continue
        try:
            data = json.loads(data_json)
        except json.JSONDecodeError:
            continue
        # data = ["garturlres", "<real_url>", ...]
        if isinstance(data, list) and len(data) >= 2 and isinstance(data[1], str):
            return data[1]
    return None


# ---------------------------------------------------------------------------
# HTTP リトライ・日付正規化（jquants/index アダプタの作法に倣う）
# ---------------------------------------------------------------------------
async def _get_with_retry(client: httpx.AsyncClient, url: str) -> str:
    """1 リクエストを 429/一時失敗のリトライ付きで GET し、本文テキストを返す。

    throttle はモジュールグローバルの直近リクエスト時刻で最低間隔（news_min_interval_seconds）を
    空ける（index.py の `_throttle` に倣う）。リトライは指数バックオフ（jquants.py に倣う）。
    """
    last_error: str | None = None
    for attempt in range(_MAX_RETRIES):
        await _throttle()
        try:
            resp = await client.get(url)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            await asyncio.sleep(_RETRY_BASE_SLEEP * (2**attempt))
            continue
        if resp.status_code == 429:
            last_error = "429 Too Many Requests"
            await asyncio.sleep(_RETRY_BASE_SLEEP * (2**attempt))
            continue
        if resp.status_code >= 400:
            raise NewsAdapterError(f"GET {url} が {resp.status_code}: {resp.text[:200]}")
        return resp.text
    raise NewsAdapterError(f"GET {url} が {_MAX_RETRIES} 回失敗しました（最後: {last_error}）。")


# プロセス共有のスロットル時刻（全フェッチ横断・monotonic 時刻）。Google への過剰アクセスを避ける。
_last_request_ts: float = 0.0
_throttle_lock = asyncio.Lock()


async def _throttle() -> None:
    """前回リクエストから最低 news_min_interval_seconds あける（index.py の _throttle に倣う）。"""
    import time

    global _last_request_ts
    min_interval = settings.news_min_interval_seconds
    async with _throttle_lock:
        wait = min_interval - (time.monotonic() - _last_request_ts)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request_ts = time.monotonic()


def _parse_pubdate(raw: str | None) -> str | None:
    """RSS の pubDate（RFC822）を 'YYYY-MM-DD' に正規化する。解析不能なら None。"""
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%d")
