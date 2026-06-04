"""J-Quants API V2 アダプタ（日本株・ETF / 日足・銘柄マスタ）。

ADR-008/010: V2（`x-api-key` 方式）を使い、データ取得はアダプタ越しにする。
V2 のレスポンスは略記フィールド（O/H/L/C/Vo/AdjC…）でエンベロープは {"data":[...],
"pagination_key":...}（docs/jquants.md）。ただし実フィールド名は要再確認のため、
正規化は「候補キーのフォールバック」にして略記でもフルネームでも拾えるようにし、
**外部キー名 → 内部列名の対応はこの 1 ファイルに閉じ込める**（DB 列は安定した内部名）。
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.jquants.com"
# 429 リトライ: Free は継続超過すると**約5分の全リクエストブロック**に入る（docs/jquants.md §4）。
# 旧設定（最大16秒=2×2^3）では乗り切れず、本番投入の実走で fetch_quotes が 429×4 で死んだ
# （2026-06-04・roadmap.md Phase 1）。上限を引き上げ、合計待機が約6分（2+4+8+16+32+64+120+120）に
# 達するまでリトライしてブロックを耐える（ADR-018 頑健化）。
_MAX_RETRIES = 8
_RETRY_BASE_SLEEP = 2.0  # 秒。429 時の指数バックオフ基数（base × 2^attempt）
_MAX_RETRY_SLEEP = 120.0  # 1 回あたりの待機上限（5分ブロックを跨ぐため長め）
# 1 リクエストの HTTP タイムアウト（秒）。全銘柄×1日（約4400行）の応答は通常数秒だが、
# 長丁場ではサーバ側の一時遅延が出る。30 秒は本番投入の実走で単発 ReadTimeout を踏んだため
# 60 秒に引き上げ、さらに _get_with_retry で TimeoutException を握って再試行する（2026-06-04）。
_HTTP_TIMEOUT = 60.0
# Free は 5 req/分。**16 秒間隔なら任意の 60 秒窓で最大 4 req** に収まり余裕を持って下回る。
# 13 秒（4.6 req/分）は窓境界で 5 req に達し、実走でブロックを誘発した（2026-06-04 本番投入）。
# 有料プランは settings.jquants_min_interval_seconds で短くする（Light=1.0・ADR-008・L-6）。
_MIN_INTERVAL_SECONDS = 16.0


class JQuantsError(RuntimeError):
    """J-Quants 取得時のエラー（キー未設定・HTTP 失敗など）。"""


class JQuantsCoverageError(JQuantsError):
    """要求した日付が契約プランの提供範囲外（2026-06-04 本番投入で判明）。

    Free は 12 週遅延＋約2年格納のため、提供範囲外の日付（直近 ~12 週・古すぎる日）は**空レスでなく
    400** で返る（例: `{"message": "Your subscription covers the following dates: 2024-03-12 ~
    2026-03-12 ..."}`）。範囲外＝「営業日の前線に到達」であって取得失敗ではないため、別例外にして
    呼び出し側（fetch_quotes）が日付ループを正常に打ち切れるようにする（calendar の「祝日は空レスで
    吸収」とは別系統）。"""


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


def _to_float(value: Any) -> float | None:
    """数値文字列を float にする。空文字 '' / None / 変換不能は None（/v2/fins/summary 対策）。

    財務サマリ（/v2/fins/summary）は値を文字列（'232.55'）で返し、N/A は空文字 '' になる。
    Float 列に '' を入れないよう、ここで None 化する（実機確認 2026-06）。
    """
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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

    # プロセス共有のスロットル時刻（全インスタンス・全ジョブ横断・monotonic 時刻）。
    # 夜間バッチはジョブごとにアダプタを作り直すため、インスタンス変数だと _last_request_ts=0 に
    # リセットされ、sync_master→fetch_quotes の境界で 2 連続リクエストのバーストが出て 5 req/分の
    # 窓を超えブロックを誘発した（2026-06-04 本番投入）。クラス変数で直前リクエスト時刻を共有し、
    # 単一プロセス＝単一レート上限の規律（ADR-002/005）どおり境界バーストを防ぐ。
    _last_request_ts: float = 0.0

    def __init__(self, api_key: str | None = None, base_url: str = _BASE_URL) -> None:
        self._api_key = api_key if api_key is not None else settings.jquants_api_key
        if not self._api_key:
            raise JQuantsError(
                "JQUANTS_API_KEY が未設定です。backend/.env に V2 の API キーを設定してください。"
            )
        self._base_url = base_url
        # スロットル間隔は設定から読む（Free=16.0 / Light=1.0・ADR-008・spec §3.4・L-6）。
        # モジュール定数 _MIN_INTERVAL_SECONDS はフォールバック既定。
        self._min_interval = settings.jquants_min_interval_seconds or _MIN_INTERVAL_SECONDS

    def _throttle(self) -> None:
        """前回リクエストから最低 self._min_interval あける（Free 5 req/分対策）。

        待機時刻はクラス変数 `_last_request_ts` に持ち、全インスタンス・全ジョブで共有する
        （ジョブ境界の連続バースト防止・2026-06-04 本番投入の知見）。
        """
        wait = self._min_interval - (time.monotonic() - JQuantsAdapter._last_request_ts)
        if wait > 0:
            time.sleep(wait)
        JQuantsAdapter._last_request_ts = time.monotonic()

    def _get_paginated(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """pagination_key を辿って全ページの行を集約する。429/一時通信失敗はバックオフで再試行。"""
        headers = {"x-api-key": self._api_key}
        rows: list[dict[str, Any]] = []
        page_params = dict(params)
        with httpx.Client(
            base_url=self._base_url, headers=headers, timeout=_HTTP_TIMEOUT
        ) as client:
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
        """1 リクエストをリトライ付きで実行する。

        リトライ対象は (1) 429 レート制限（約5分ブロックを跨ぐ長めの待機）、(2) 一時的な通信失敗
        （`httpx.TimeoutException`・`httpx.TransportError`＝ReadTimeout/ConnectError 等）。
        本番投入の実走で 429 を耐えた後に単発 ReadTimeout でジョブが死んだ（2026-06-04・約500 req の
        長丁場では一時タイムアウトは不可避）。これも握って指数バックオフで再試行する（ADR-018）。
        """
        last_error: str | None = None
        for attempt in range(_MAX_RETRIES):
            self._throttle()
            sleep_s = min(_MAX_RETRY_SLEEP, _RETRY_BASE_SLEEP * (2**attempt))
            try:
                resp = client.get(path, params=params)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "J-Quants 通信失敗 (%s): %d/%d 回目・%s・%.0f秒待機して再試行",
                    path,
                    attempt + 1,
                    _MAX_RETRIES,
                    type(exc).__name__,
                    sleep_s,
                )
                time.sleep(sleep_s)
                continue
            if resp.status_code == 429:  # レート制限。約5分ブロックを跨ぐため長めに待って再試行
                last_error = "429 Too Many Requests"
                logger.warning(
                    "J-Quants 429 (%s): %d/%d 回目・%.0f秒待機して再試行",
                    path,
                    attempt + 1,
                    _MAX_RETRIES,
                    sleep_s,
                )
                time.sleep(sleep_s)
                continue
            if resp.status_code >= 400:
                # 範囲外日付の 400（"... covers the following dates ..."）は前線到達＝正常終了の
                # 合図。呼び出し側が打ち切れるよう別例外で送出する（2026-06-04 本番投入の知見）。
                if resp.status_code == 400 and "covers the following dates" in resp.text:
                    raise JQuantsCoverageError(f"GET {path} 契約範囲外: {resp.text[:200]}")
                raise JQuantsError(f"GET {path} が {resp.status_code}: {resp.text[:200]}")
            return resp.json()
        raise JQuantsError(f"GET {path} が {_MAX_RETRIES} 回失敗しました（最後: {last_error}）。")

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

        実 API 確認済み（2026-06）: `code` を渡さず `date` だけ指定すると、その日の東証全銘柄
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

    def fetch_master_all(self) -> list[dict[str, Any]]:
        """全銘柄マスタを `code` 無しで一括取得する（spec §3.6・sync_master）。

        `bars/daily` の日付一括と同じパターンで `/v2/equities/master` を `code` を渡さずに叩き、
        全銘柄（約4000・ETF/REIT 含む）を 1〜数 req で取得することを試みる版。`fetch_master`
        （1 件ずつループ＝全件で14時間超）の代替（裁定 L-5）。

        全件返れば正規化して返す。空配列が返った場合（このエンドポイントが code 必須だった等）も
        そのまま `[]` を返し、呼び出し側（sync_master）が daily の code 補完にフォールバックできる
        ようにする。HTTP 失敗は JQuantsError として送出（呼び出し側が握る）。
        """
        now = datetime.now(UTC).isoformat(timespec="seconds")
        raw = self._get_paginated("/v2/equities/master", {})
        return [self._normalize_stock(r, now) for r in raw]

    @staticmethod
    def _normalize_stock(r: dict[str, Any], fetched_at: str) -> dict[str, Any]:
        # V2 /v2/equities/master の実フィールド名（実 API 確認済み 2026-06）:
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

    # --- 財務・決算（Phase 2・0005_financials） --------------------------------

    def fetch_financials(
        self,
        code: str | None = None,
        date: str | None = None,
    ) -> list[dict[str, Any]]:
        """財務・決算データを取得し、内部列名に正規化して返す（phase2-spec.md §3.2・ADR-031）。

        エンドポイントは **/v2/fins/summary**（実機確認 2026-06。/v2/fins/statements は 403）。
        `code` 指定で 1 銘柄（過去複数期の開示行）、`date` 指定でその日開示の全銘柄を取得する。
        フィールドは短縮名（EPS/BPS/Sales/OP/NP/DivAnn/FDivAnn/ShOutFY/TrShFY/DiscDate/...）で、
        値は文字列・N/A は空文字。`_first` で候補キー対応、`_to_float` で数値化する。
        """
        params: dict[str, Any] = {}
        if code:
            params["code"] = _to_jq_code(code)
        if date:
            params["date"] = date
        raw = self._get_paginated("/v2/fins/summary", params)
        return [self._normalize_financial(r) for r in raw]

    @staticmethod
    def _normalize_financial(r: dict[str, Any]) -> dict[str, Any]:
        """財務行を内部列名に正規化する（phase2-spec.md §3.2・ADR-031・外部キー名→内部列名）。

        /v2/fins/summary の実フィールド名（実機確認 2026-06。候補順は「実名 → 旧フルネーム」）:
          EPS/BPS/Sales/OP/NP・DiscDate（開示日）・CurPerType（会計期間種別 'FY'/'1Q'…）。
          配当は FDivAnn（予想年間）優先・DivAnn（実績年間）フォールバック＝予想配当利回りを既定。
          発行済株式数 ShOutFY・自己株式 TrShFY → 時価総額 = close * (ShOutFY - TrShFY)。
        値は文字列・N/A は空文字のため数値は `_to_float` で None 化する。
        """
        return {
            "code": _first(r, ["Code", "code"]),
            "disclosed_date": _norm_date(_first(r, ["DiscDate", "DisclosedDate", "Date"])),
            # CurPerType: 'FY', '1Q', '2Q', '3Q' 等（実機確認 2026-06）
            "fiscal_period": _first(r, ["CurPerType", "TypeOfCurrentPeriod", "fiscal_period"]),
            "net_sales": _to_float(_first(r, ["Sales", "NetSales", "net_sales"])),
            "operating_profit": _to_float(_first(r, ["OP", "OperatingProfit", "operating_profit"])),
            "profit": _to_float(_first(r, ["NP", "Profit", "NetIncome", "profit"])),
            "eps": _to_float(_first(r, ["EPS", "EarningsPerShare", "eps"])),
            "bps": _to_float(_first(r, ["BPS", "BookValuePerShare", "bps"])),
            # 配当は予想（FDivAnn）優先・実績（DivAnn）フォールバック＝予想配当利回りを既定に
            "dividend_per_share": _to_float(
                _first(r, ["FDivAnn", "DivAnn", "DividendPerShareAnnual"])
            ),
            "shares_outstanding": _to_float(
                _first(r, ["ShOutFY", "NumberOfIssuedAndOutstandingShares"])
            ),
            "treasury_shares": _to_float(_first(r, ["TrShFY", "NumberOfTreasuryStock"])),
        }
