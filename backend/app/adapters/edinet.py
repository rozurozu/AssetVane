"""EDINET アダプタ（EdinetAdapter）— 有報「事業の内容」テキスト源（テーマタグ段階C）。

設計の真実: docs/decisions.md ADR-056（EDINET を JP の事業説明テキスト源にする）・ADR-050 改訂
（全ユニバース grounded 事前タグ・段階C＝EDINET → JP 全ユニバース）・ADR-010（アダプタ越し）。

ADR-056: 価格・財務・銘柄マスタは J-Quants V2 のまま（ADR-008）。EDINET は**テキスト専用の
        additive ソース**で置換ではない。本アダプタは「銘柄→最新有報の docID を提出日クロールで
        解決」ではなく、**提出日 1 日分の書類一覧を返す + docID から事業の内容テキストを抜く**の
        2 メソッドに割り、クロール（どの日を舐めるか）の制御は呼び出し側（batch ジョブ）に委ねる。
        EDINET 書類一覧 API は提出日でしか引けないため、銘柄単位の最新解決はクロールの帰結になる。
ADR-010: 外部 API アクセス・「外部キー名→内部列名」の正規化・リトライ/スロットルをこの 1 ファイルに
        閉じ込める。adapter は DB にも LLM にも触らない（要約は呼び出し側 advisor/edinet_summary）。
ADR-005: API キー（Subscription-Key）は backend の .env のみ（settings 経由・ハードコード禁止）。

EDINET API v2:
  - 書類一覧 API: GET {base}/documents.json?date=YYYY-MM-DD&type=2&Subscription-Key=<key>
    → {"metadata": ..., "results": [ {docID, secCode, docTypeCode, filerName, periodEnd,
       submitDateTime, csvFlag, ...}, ... ]}。docTypeCode='120'=有価証券報告書。
  - 書類取得 API: GET {base}/documents/{docID}?type=5&Subscription-Key=<key>
    → CSV を ZIP で返す。ZIP 内の CSV は **UTF-16・タブ区切り**（列: 要素ID/項目名/コンテキストID/
       相対年度/連結個別/期間時点/ユニットID/単位/値）。事業の内容は要素ID
       `jpcrp_cor:DescriptionOfBusinessTextBlock` の「値」列（HTML 断片）。
"""

from __future__ import annotations

import csv
import html
import io
import logging
import re
import time
import zipfile
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# --- 定数（フォールバック既定。実値は settings から読む） ------------------------
_MAX_RETRIES = 3
_RETRY_BASE_SLEEP = 2.0  # 秒。429/一時失敗時の指数バックオフ基数（base × 2^attempt）

# 有価証券報告書の docTypeCode（EDINET コードリスト・事業の内容を含む年次開示）。
DOC_TYPE_ANNUAL_SECURITIES_REPORT = "120"

# 事業の内容（XBRL/CSV の要素ID）。CSV（type=5）の「要素ID」列がこの値の行に本文が入る。
_BUSINESS_ELEMENT_ID = "jpcrp_cor:DescriptionOfBusinessTextBlock"

# EDINET CSV（type=5）のエンコーディングと区切り（UTF-16・タブ・2026-06 仕様）。
_CSV_ENCODING = "utf-16"
_CSV_DELIMITER = "\t"

# HTML タグ除去（事業の内容は <p>/<table> 等を含む TextBlock のため軽く strip する）。
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t　]+")
_BLANKLINES_RE = re.compile(r"\n{3,}")


class EdinetAdapterError(RuntimeError):
    """EdinetAdapter の取得エラー（書類一覧/取得そのものの失敗＝ハード失敗）。

    呼び出し側（batch ジョブ）が docID 境界・提出日境界で握って後続を止めない（ADR-018）。
    メッセージには対処（.env の EDINET_API_KEY 設定等）を含める。
    """


def _strip_html(raw: str) -> str:
    """事業の内容の HTML 断片をプレーンテキストへ軽く整形する（要約前の下ごしらえ）。

    タグ除去 → HTML エンティティ復元 → 連続空白/空行の畳み込み。本文の意味は保ち、
    要約 LLM とタガーの evidence 照合（advisor/theme_tagger）に渡せる素のテキストにする。
    """
    text = _HTML_TAG_RE.sub("\n", raw)
    text = html.unescape(text)
    # 行ごとに端の空白を落とし、3 連以上の空行を 2 行に畳む（読みやすさ・トークン節約）。
    lines = [_WS_RE.sub(" ", line).strip() for line in text.splitlines()]
    text = "\n".join(lines)
    text = _BLANKLINES_RE.sub("\n\n", text)
    return text.strip()


class EdinetAdapter:
    """EDINET API v2 のクライアント（書類一覧 + 事業の内容抽出・ADR-010/056）。

    状態（スロットル時刻）はインスタンスに持つ（モジュールグローバル共有を避ける＝adapter 規約）。
    HTTP は httpx.Client（同期）。呼び出し側の batch ジョブが同期 def なので async にしない
    （要約 LLM だけ呼び出し側が asyncio.run する＝tag_jp_themes 同型）。
    """

    def __init__(self, *, api_key: str | None = None) -> None:
        self._api_key = api_key if api_key is not None else settings.edinet_api_key
        self._base_url = settings.edinet_base_url
        self._timeout = settings.edinet_http_timeout_seconds
        self._min_interval = settings.edinet_min_interval_seconds
        self._last_request_ts = 0.0

    def _throttle(self) -> None:
        """前回リクエストから最低間隔を空ける（EDINET への過剰アクセスを避ける・ADR-010）。"""
        wait = self._min_interval - (time.monotonic() - self._last_request_ts)
        if wait > 0:
            time.sleep(wait)
        self._last_request_ts = time.monotonic()

    def _params(self, extra: dict[str, Any]) -> dict[str, Any]:
        """共通クエリ（Subscription-Key）に呼び出し別パラメータを足す（キーはハードコードしない）。"""
        params = dict(extra)
        if self._api_key:
            params["Subscription-Key"] = self._api_key
        return params

    def _get_with_retry(
        self, client: httpx.Client, path: str, params: dict[str, Any]
    ) -> httpx.Response:
        """1 リクエストを 429/一時失敗のリトライ付きで GET する（news/index アダプタの作法）。"""
        last_error: str | None = None
        for attempt in range(_MAX_RETRIES):
            self._throttle()
            try:
                resp = client.get(path, params=params)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                time.sleep(_RETRY_BASE_SLEEP * (2**attempt))
                continue
            if resp.status_code == 429:
                last_error = "429 Too Many Requests"
                time.sleep(_RETRY_BASE_SLEEP * (2**attempt))
                continue
            if resp.status_code >= 400:
                raise EdinetAdapterError(
                    f"GET {path} が {resp.status_code}: {resp.text[:200]}"
                    "（EDINET_API_KEY 未設定/誤りの可能性）"
                )
            return resp
        raise EdinetAdapterError(
            f"GET {path} が {_MAX_RETRIES} 回失敗しました（最後: {last_error}）。"
        )

    def list_documents(self, date: str) -> list[dict[str, Any]]:
        """指定提出日（YYYY-MM-DD）の書類一覧を正規化して返す（書類一覧 API・type=2）。

        外部キー名（docID/secCode/docTypeCode/...）→ 内部名（doc_id/sec_code/...）の正規化を
        このアダプタに閉じ込める（ADR-010）。EDINET は障害を HTTP 200＋`metadata.status` で
        返すことがあるため、status "200" 以外は EdinetAdapterError に倒す（黙って 0 行に
        しない＝ADR-018・tasks/review-2026-06-12.md C-8）。検証を通った後の `results` 欠落は
        提出ゼロの日（休場等）＝正規の空として空リストを返す（呼び出し側はカーソルを進めてよい）。

        Returns:
            各 dict = {doc_id, sec_code, doc_type_code, filer_name, period_end,
            submit_datetime, csv_flag}。secCode は 5 桁文字列 or None（非上場提出は None）。
        """
        with httpx.Client(base_url=self._base_url, timeout=self._timeout) as client:
            resp = self._get_with_retry(
                client, "/documents.json", self._params({"date": date, "type": 2})
            )
            try:
                payload = resp.json()
            except ValueError as exc:
                raise EdinetAdapterError(
                    f"documents.json の JSON 解析に失敗（date={date}）: {exc}"
                ) from exc

        results = _extract_results(payload, date=date)
        return [_normalize_doc(raw) for raw in results if isinstance(raw, dict)]

    def fetch_business_description(self, doc_id: str) -> dict[str, Any] | None:
        """書類取得 API（type=5・CSV ZIP）から事業の内容テキストを抜く（ADR-056）。

        ZIP 内の CSV（UTF-16・タブ区切り）を走査し、要素ID
        `jpcrp_cor:DescriptionOfBusinessTextBlock` の「値」列を取り、HTML を軽く strip して返す。
        本文は呼び出し側が要約後に捨てる（ADR-020）。事業の内容が無い書類（型違い等）は None。

        Returns:
            `{doc_id, text}`（text は strip 済みプレーンテキスト）。見つからなければ None。
        """
        with httpx.Client(base_url=self._base_url, timeout=self._timeout) as client:
            resp = self._get_with_retry(client, f"/documents/{doc_id}", self._params({"type": 5}))
            content = resp.content

        try:
            text = _extract_business_text(content)
        except (zipfile.BadZipFile, OSError) as exc:
            raise EdinetAdapterError(f"書類 {doc_id} の ZIP 解凍に失敗: {exc}") from exc

        if not text:
            return None
        return {"doc_id": doc_id, "text": text}


def _extract_results(payload: Any, *, date: str) -> list[Any]:
    """書類一覧レスポンスから results を取り出す（metadata.status 検証込み・ADR-018）。

    EDINET API は障害を HTTP 200＋`metadata.status`（"200" 以外）で返すことがある。これを
    「提出ゼロの日」と同一視するとクロールカーソルが前進し、その提出日の有報を静かに
    取りこぼすため、status "200" 以外は EdinetAdapterError に倒す（黙って 0 行にしない＝
    ADR-018・tasks/review-2026-06-12.md C-8）。検証を通った後の `results` 欠落（キー無し/None）
    は提出ゼロの日＝正規の空として [] を返す。status "200" なのに results が list でない形は
    想定外の応答としてこれも raise する（取りこぼし防止を優先）。
    """
    if not isinstance(payload, dict):
        raise EdinetAdapterError(
            f"documents.json の応答が想定外（date={date}・{type(payload).__name__}）"
        )
    metadata = payload.get("metadata")
    status = metadata.get("status") if isinstance(metadata, dict) else None
    # EDINET v2 の status は文字列 "200" だが、数値で返る変化にも備えて str 比較で吸収する。
    if str(status) != "200":
        message = metadata.get("message") if isinstance(metadata, dict) else None
        raise EdinetAdapterError(
            f"documents.json が異常応答（date={date}・metadata.status={status!r}"
            f"・message={message!r}）。提出ゼロの日とは区別しカーソルを進めない。"
        )
    results = payload.get("results")
    if results is None:
        return []
    if not isinstance(results, list):
        raise EdinetAdapterError(
            f"documents.json の results が list でない（date={date}・{type(results).__name__}）"
        )
    return results


def _normalize_doc(raw: dict[str, Any]) -> dict[str, Any]:
    """書類一覧の 1 件を外部キー名→内部名に正規化する（ADR-010 の境界・キー名はここに閉じる）。"""
    return {
        "doc_id": raw.get("docID"),
        "sec_code": raw.get("secCode"),
        "doc_type_code": raw.get("docTypeCode"),
        "filer_name": raw.get("filerName"),
        "period_end": raw.get("periodEnd"),
        "submit_datetime": raw.get("submitDateTime"),
        "csv_flag": raw.get("csvFlag"),
    }


def _extract_business_text(zip_bytes: bytes) -> str:
    """CSV ZIP（type=5）から事業の内容テキスト（最初の非空一致）を取り出す。

    ZIP 内には本表・監査報告書等の複数 CSV が入る。要素ID 列が
    `jpcrp_cor:DescriptionOfBusinessTextBlock` の行を全 CSV から探し、最初の非空の値を採る。
    CSV は UTF-16・タブ区切り（EDINET 仕様）。HTML 断片は _strip_html でプレーン化する。
    """
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in zf.namelist():
            if not name.lower().endswith(".csv"):
                continue
            raw = zf.read(name)
            try:
                decoded = raw.decode(_CSV_ENCODING)
            except UnicodeDecodeError:
                # 一部書類は UTF-8 BOM の可能性に備えフォールバック（仕様外でも握って続行）。
                decoded = raw.decode("utf-8-sig", errors="ignore")
            reader = csv.reader(io.StringIO(decoded), delimiter=_CSV_DELIMITER)
            for row in reader:
                if not row:
                    continue
                if row[0] == _BUSINESS_ELEMENT_ID and len(row) >= 2:
                    value = (row[-1] or "").strip()
                    if value:
                        return _strip_html(value)
    return ""
