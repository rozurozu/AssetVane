"""主要指数アダプタ（IndexAdapter）。

ADR-010: データソースはアダプタ越し。Stooq を既定のソースとして使用する（spec §3.1・裁定 L-10）。
Stooq CSV 形式: `https://stooq.com/q/d/l/?s=<symbol>&i=d`
CSV ヘッダ: Date,Open,High,Low,Close,Volume（終値のみ使用）。
J-Quants 範囲外（主要指数・米国株）なので別ソース（phase2-spec.md §3.1）。

外部キー名 → 内部列名の対応をこのファイルに閉じ込める（ADR-010）。
HTTP 失敗は独自例外 IndexAdapterError で送出する。
"""

from __future__ import annotations

import csv
import io
import time
from typing import Any

import httpx

from app.config import settings

_DEFAULT_BASE_URL = "https://stooq.com"
_MIN_INTERVAL_SECONDS = 1.0  # Stooq はリクエスト間隔を 1 秒程度あければ十分
_MAX_RETRIES = 3
_RETRY_BASE_SLEEP = 2.0


class IndexAdapterError(RuntimeError):
    """IndexAdapter の取得エラー（HTTP 失敗・CSV パースエラー等）。"""


def _norm_date(value: Any) -> str:
    """日付を 'YYYY-MM-DD' に正規化（'20230324' / '2023-03-24' のどちらでも）。"""
    s = str(value)
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return s


class IndexAdapter:
    """主要指数の日次終値を取得するアダプタ（ADR-010・spec §3.1）。

    既定ソース: Stooq（`https://stooq.com/q/d/l/?s=<symbol>&i=d` の CSV）。
    `client` 引数でテスト用のスタブを注入できる。
    """

    def __init__(
        self,
        base_url: str = _DEFAULT_BASE_URL,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url
        self._client = client  # テスト注入用（None なら都度作成）
        self._last_request_ts = 0.0  # スロットル用（monotonic 時刻）
        # スロットル間隔は設定から読む（Stooq は 1.0 で十分・ADR-010）。
        # モジュール定数 _MIN_INTERVAL_SECONDS はフォールバック既定。
        self._min_interval = settings.index_min_interval_seconds or _MIN_INTERVAL_SECONDS

    def _throttle(self) -> None:
        """前回リクエストから最低 self._min_interval あける。"""
        wait = self._min_interval - (time.monotonic() - self._last_request_ts)
        if wait > 0:
            time.sleep(wait)
        self._last_request_ts = time.monotonic()

    def _get_csv(self, symbol: str, from_: str | None = None, to: str | None = None) -> str:
        """Stooq から CSV テキストを取得する（spec §3.1）。

        `symbol` は Stooq シンボル（例 '^SPX'・'^NKX'・'^TPX'）。
        HTTP 失敗は IndexAdapterError に変換する。
        """
        params: dict[str, str] = {"s": symbol, "i": "d"}
        if from_:
            params["d1"] = from_.replace("-", "")
        if to:
            params["d2"] = to.replace("-", "")

        def do_request(c: httpx.Client) -> str:
            for attempt in range(_MAX_RETRIES):
                self._throttle()
                resp = c.get("/q/d/l/", params=params)
                if resp.status_code == 429:
                    time.sleep(_RETRY_BASE_SLEEP * (2**attempt))
                    continue
                if resp.status_code >= 400:
                    raise IndexAdapterError(
                        f"Stooq GET /q/d/l/ symbol={symbol} が"
                        f" {resp.status_code}: {resp.text[:200]}"
                    )
                return resp.text
            raise IndexAdapterError(
                f"Stooq GET /q/d/l/ symbol={symbol} がレート制限で {_MAX_RETRIES} 回失敗しました。"
            )

        if self._client is not None:
            return do_request(self._client)

        with httpx.Client(base_url=self._base_url, timeout=30.0) as c:
            return do_request(c)

    def fetch_index_quotes(
        self,
        symbol: str,
        from_: str | None = None,
        to: str | None = None,
    ) -> list[dict[str, Any]]:
        """指定シンボルの日次終値を取得し、内部列名に正規化して返す（spec §3.1）。

        戻り値: [{"symbol", "date", "close"}, ...] の list。
        CSV ヘッダ: Date,Open,High,Low,Close,Volume（Close のみ使用）。
        空行・ヘッダ欠落・No data 行はスキップする。
        """
        text = self._get_csv(symbol, from_=from_, to=to)
        rows: list[dict[str, Any]] = []
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            # Stooq はデータ無しの場合 "No data" 等の行を返すことがある。
            # DictReader では列数が不足する行は None を返すため strip は None ガードが必要。
            date_val = (row.get("Date") or "").strip()
            close_raw = row.get("Close")
            close_val = (close_raw or "").strip()
            if not date_val or not close_val or date_val.lower() == "no data":
                continue
            try:
                close = float(close_val)
            except ValueError:
                continue
            rows.append(
                {
                    "symbol": symbol,
                    "date": _norm_date(date_val),
                    "close": close,
                }
            )
        return rows
