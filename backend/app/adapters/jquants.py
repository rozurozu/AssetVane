"""J-Quants API V2 アダプタ（日本株・ETF / 日足・銘柄マスタ）。

ADR-008/010: V2（`x-api-key` 方式）を使い、データ取得はアダプタ越しにする。
V2 のレスポンスは略記フィールド（O/H/L/C/Vo/AdjC…）でエンベロープは {"data":[...],
"pagination_key":...}（docs/jquants.md）。ただし実フィールド名は要再確認のため、
正規化は「候補キーのフォールバック」にして略記でもフルネームでも拾えるようにし、
**外部キー名 → 内部列名の対応はこの 1 ファイルに閉じ込める**（DB 列は安定した内部名）。
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import httpx

from app.config import settings

_BASE_URL = "https://api.jquants.com"
_MAX_RETRIES = 4
_RETRY_BASE_SLEEP = 2.0  # 秒。429 時に指数バックオフ
# Free は 5 req/分。リクエスト間を最低この秒数あけて 429（＋約5分のブロック）を防ぐ。
# 有料プランなら短くできるが、まずは安全側に倒す（Phase 0 は数銘柄なので速度は問題にならない）。
_MIN_INTERVAL_SECONDS = 13.0


class JQuantsError(RuntimeError):
    """J-Quants 取得時のエラー（キー未設定・HTTP 失敗など）。"""


def _first(d: dict[str, Any], keys: list[str], default: Any = None) -> Any:
    """候補キーのうち最初に存在したものの値を返す（V2 略記/V1 フルネーム両対応）。"""
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def _to_jq_code(code: str) -> str:
    """4 桁コードを J-Quants の 5 桁形式に正規化（例 7203 → 72030）。既に 5 桁ならそのまま。"""
    code = code.strip()
    return f"{code}0" if len(code) == 4 and code.isdigit() else code


def _norm_date(value: Any) -> str:
    """日付を 'YYYY-MM-DD' に正規化（'20230324' / '2023-03-24' のどちらでも）。"""
    s = str(value)
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return s


def _extract_rows(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
    """エンベロープから行リストと pagination_key を取り出す。

    V2 は "data" 想定だが、念のため「pagination_key 以外で最初の list 値」も拾う。
    """
    pagination_key = payload.get("pagination_key")
    if isinstance(payload.get("data"), list):
        return payload["data"], pagination_key
    for key, val in payload.items():
        if key != "pagination_key" and isinstance(val, list):
            return val, pagination_key
    return [], pagination_key


class JQuantsAdapter:
    """J-Quants V2 クライアント。`code` と期間を渡すと正規化済みの行を返す（ADR-010）。"""

    def __init__(self, api_key: str | None = None, base_url: str = _BASE_URL) -> None:
        self._api_key = api_key if api_key is not None else settings.jquants_api_key
        if not self._api_key:
            raise JQuantsError(
                "JQUANTS_API_KEY が未設定です。backend/.env に V2 の API キーを設定してください。"
            )
        self._base_url = base_url
        self._last_request_ts = 0.0  # スロットル用（monotonic 時刻）

    def _throttle(self) -> None:
        """前回リクエストから最低 _MIN_INTERVAL_SECONDS あける（Free 5 req/分対策）。"""
        wait = _MIN_INTERVAL_SECONDS - (time.monotonic() - self._last_request_ts)
        if wait > 0:
            time.sleep(wait)
        self._last_request_ts = time.monotonic()

    def _get_paginated(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """pagination_key を辿って全ページの行を集約する。429 は指数バックオフでリトライ。"""
        headers = {"x-api-key": self._api_key}
        rows: list[dict[str, Any]] = []
        page_params = dict(params)
        with httpx.Client(base_url=self._base_url, headers=headers, timeout=30.0) as client:
            while True:
                payload = self._get_with_retry(client, path, page_params)
                page_rows, pagination_key = _extract_rows(payload)
                rows.extend(page_rows)
                if not pagination_key:
                    break
                page_params["pagination_key"] = pagination_key
        return rows

    def _get_with_retry(
        self, client: httpx.Client, path: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        for attempt in range(_MAX_RETRIES):
            self._throttle()
            resp = client.get(path, params=params)
            if resp.status_code == 429:  # レート制限。待って再試行
                time.sleep(_RETRY_BASE_SLEEP * (2**attempt))
                continue
            if resp.status_code >= 400:
                raise JQuantsError(f"GET {path} が {resp.status_code}: {resp.text[:200]}")
            return resp.json()
        raise JQuantsError(f"GET {path} がレート制限で {_MAX_RETRIES} 回失敗しました。")

    # --- 日足 ---------------------------------------------------------------

    def fetch_daily_quotes(
        self, code: str, from_: str | None = None, to: str | None = None
    ) -> list[dict[str, Any]]:
        """指定銘柄の日足を取得し、内部列名に正規化して返す。"""
        params: dict[str, Any] = {"code": _to_jq_code(code)}
        if from_:
            params["from"] = from_
        if to:
            params["to"] = to
        raw = self._get_paginated("/v2/equities/bars/daily", params)
        return [self._normalize_quote(r) for r in raw]

    def fetch_daily_quotes_by_date(self, date: str) -> list[dict[str, Any]]:
        """指定 1 営業日の**全銘柄**の日足を取得する（内部列名に正規化）。

        実機確認済み（2026-06）: `code` を渡さず `date` だけ指定すると、その日の東証全銘柄
        （ETF/REIT 含む・約 4400 行）が 1 リクエスト（＋ページング）で返る。これにより Phase 1
        の初回バックフィルを「銘柄ループ（約4000 req・13時間超）」ではなく「**営業日ループ**
        （2年で約 500 req）」で回せる（docs/jquants.md §4）。`date` は 'YYYY-MM-DD' / 'YYYYMMDD'。
        """
        raw = self._get_paginated("/v2/equities/bars/daily", {"date": date})
        return [self._normalize_quote(r) for r in raw]

    @staticmethod
    def _normalize_quote(r: dict[str, Any]) -> dict[str, Any]:
        return {
            "code": _first(r, ["Code", "code"]),
            "date": _norm_date(_first(r, ["Date", "date"])),
            "open": _first(r, ["O", "Open", "open"]),
            "high": _first(r, ["H", "High", "high"]),
            "low": _first(r, ["L", "Low", "low"]),
            "close": _first(r, ["C", "Close", "close"]),
            "volume": _first(r, ["Vo", "Volume", "volume"]),
            "adj_close": _first(r, ["AdjC", "AdjustmentClose", "adj_close"]),
        }

    # --- 銘柄マスタ ---------------------------------------------------------

    def fetch_master(self, codes: list[str]) -> list[dict[str, Any]]:
        """指定コードの銘柄マスタを取得し、内部列名に正規化して返す。"""
        now = datetime.now(UTC).isoformat(timespec="seconds")
        out: list[dict[str, Any]] = []
        for code in codes:
            raw = self._get_paginated("/v2/equities/master", {"code": _to_jq_code(code)})
            out.extend(self._normalize_stock(r, now) for r in raw)
        return out

    @staticmethod
    def _normalize_stock(r: dict[str, Any], fetched_at: str) -> dict[str, Any]:
        # V2 /v2/equities/master の実フィールド名（実機確認済み 2026-06）:
        #   Code / CoName / CoNameEn / S17 / S17Nm / S33 / S33Nm / ScaleCat / Mkt / MktNm / ...
        # ETF/REIT 判別は Mkt（市場区分）コードの対応表が要るが、Phase 0 の対象（プライム大型株）は
        # 普通株なので 0 で正しい。ETF を扱う Phase 7（TOPIX-17 ETF）で Mkt→is_etf の対応を足す。
        is_etf = 0
        return {
            "code": _first(r, ["Code", "code"]),
            "company_name": _first(r, ["CoName", "CompanyName", "Name", "company_name"]),
            "sector33_code": _first(r, ["S33", "Sector33Code", "sector33_code"]),
            "sector17_code": _first(r, ["S17", "Sector17Code", "sector17_code"]),
            "market_code": _first(r, ["Mkt", "MarketCode", "market_code"]),
            "is_etf": is_etf,
            "updated_at": fetched_at,
        }
