"""同期アダプタ共通の HTTP 補助（スロットル・リトライ・ADR-010）。

backend-adapter-pattern: スロットル＝前回リクエストからの最低間隔を time.monotonic で計り
time.sleep で待つ／リトライ＝429・一時失敗に指数バックオフ、最大回数を定数で持つ、を
このモジュールにコンポーネント化する。各アダプタ／ソースは Throttle をインスタンスに保持し
（状態をモジュールグローバルで共有しない＝イベントループ／プロセス跨ぎで壊れるのを避ける）、
get_with_retry に throttle を渡して 1 リクエストを回す。用途別の独自例外メッセージ（対処を含む）
と返り型の取り出しはアダプタ側に残すため、HTTP エラー時／リトライ枯渇時の例外生成をコールバック
で受け取り、返り値は httpx.Response に統一する。

意図的な対象外（個別実装に残す）:
  - JQuantsAdapter の throttle（クラス変数・ジョブ境界バースト防止）とリトライ（多めの再試行・
    長い上限＝Free プランの長時間ブロック耐性）は事情が違うのでここに寄せない。
  - news.py の async throttle（イベントループ Lock workaround）／async リトライも別物。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

# 同期アダプタ共通のリトライ既定（backend-adapter-pattern: 最大回数は定数で持つ）。
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BASE_SLEEP = 2.0  # 秒。429/一時失敗の指数バックオフ基数（base × 2**attempt）


class Throttle:
    """前回リクエストからの最低間隔を空けるスロットル（コンポジションで共有・ADR-010）。

    状態（最終リクエスト時刻）をこのインスタンスに閉じ込める。アダプタ／ソースは
    `self._throttle = Throttle(interval)` として保持し、リクエスト直前に `wait()` を呼ぶ
    （get_with_retry に渡すと各 attempt の直前に呼ばれる）。モジュールグローバルの可変状態・
    asyncio.Lock をソース横断で共有しない（backend-adapter-pattern：イベントループ／プロセス
    跨ぎで壊れるのを避ける）。Throttle はアダプタインスタンスごとに別個なので共有は起きない。
    """

    def __init__(self, min_interval_seconds: float) -> None:
        self._min_interval = min_interval_seconds
        self._last_request_ts = 0.0

    def wait(self) -> None:
        """前回 wait() から最低 min_interval 秒あける（monotonic で計測・time.sleep で待つ）。"""
        remaining = self._min_interval - (time.monotonic() - self._last_request_ts)
        if remaining > 0:
            time.sleep(remaining)
        self._last_request_ts = time.monotonic()


def get_with_retry(
    client: httpx.Client,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    throttle: Throttle | None = None,
    on_http_error: Callable[[httpx.Response], Exception],
    on_exhausted: Callable[[str | None], Exception],
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_base_sleep: float = DEFAULT_RETRY_BASE_SLEEP,
    retry_network_errors: bool = True,
) -> httpx.Response:
    """1 リクエストを 429／一時失敗のリトライ付きで GET し Response を返す（ADR-010）。

    各 attempt の直前に throttle.wait() を呼ぶ（既存アダプタの「スロットル→GET」順を保つ）。
    429 は指数バックオフ（retry_base_sleep × 2**attempt）でリトライ。retry_network_errors=True
    なら httpx.TimeoutException/TransportError も同様にリトライ、False なら捕まえず呼び出し側へ
    透過する（429 のみリトライする既存アダプタ＝fund_nav/index-Stooq の挙動を保存する）。429 以外
    の 4xx/5xx は on_http_error(resp) が返す例外で即 raise（用途別の独自例外＝EdinetAdapterError
    等へ翻訳）。max_retries 回尽きたら on_exhausted(last_error) を raise。返り値は httpx.Response
    （呼び出し側で .content/.text/.json() を取る＝返り型の差はここで持たない）。

    throttle はループ内から呼び、リトライ間隔（バックオフ）と最低間隔（スロットル）の双方を
    attempt ごとに効かせる（既存アダプタの挙動の再現・backend-adapter-pattern）。
    """
    last_error: str | None = None
    for attempt in range(max_retries):
        if throttle is not None:
            throttle.wait()
        try:
            resp = client.get(path, params=params or {})
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            if not retry_network_errors:
                raise
            last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(retry_base_sleep * (2**attempt))
            continue
        if resp.status_code == 429:
            last_error = "429 Too Many Requests"
            time.sleep(retry_base_sleep * (2**attempt))
            continue
        if resp.status_code >= 400:
            raise on_http_error(resp)
        return resp
    raise on_exhausted(last_error)
