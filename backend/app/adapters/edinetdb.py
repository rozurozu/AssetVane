"""EDINET DB（edinetdb.jp）アダプタ — #2 売掛/在庫の質の構造化財務源（ADR-064）。

設計の真実: docs/decisions.md ADR-064（手元キーは公式 EDINET でなく第三者 edinetdb.jp のもの＝#2 は
edinetdb.jp の構造化財務を使う）・ADR-010（アダプタ越し・外部キー名→内部名の正規化をここに閉じる）・
ADR-014（事実はコード・解釈は LLM）。

公式 EDINET（adapters/edinet.py・api.edinet-fsa.go.jp・テキスト源）とは**別系統**。本アダプタは
正規化済みの財務（trade_receivables/inventories/revenue/gross_profit 等）を銘柄コード直引きで取る。

edinetdb.jp API v1（実機確認 2026-06-30）:
  - base: https://edinetdb.jp/v1・認証: ヘッダ `X-API-Key`
  - レート制限: ヘッダ x-ratelimit-{remaining,limit}（日次）・
  x-ratelimit-monthly-{remaining,limit}。
    実予算の enforce は呼び出し側（夜間ジョブ）が `last_budget` を見て行う（ADR-064・
    無料枠は日100/月600）。
  - GET /companies?q=<sec_code> … 1 件ピンポイント解決（q は sec_code でも効く）。data[].{sec_code,
    edinet_code,...}・meta.pagination。sec_code は 5 桁（stocks.code と同形・例 トヨタ '72030'）。
  - GET /companies/{edinet_code}/financials … 財務時系列（data[]・年次・古い順）。近年の行に詳細 BS
    （trade_receivables/inventories/gross_profit）、古い年はサマリのみ＝年次で項目数が可変。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.adapters._http import DEFAULT_MAX_RETRIES, Throttle, get_with_retry

logger = logging.getLogger(__name__)

_BASE_URL = "https://edinetdb.jp/v1"
_DEFAULT_TIMEOUT = 30.0


class EdinetDbAdapterError(RuntimeError):
    """EdinetDbAdapter の取得エラー（認証・HTTP 失敗・想定外応答＝ハード失敗）。

    呼び出し側（#2 夜間ジョブ）が銘柄境界で握って後続を止めない（ADR-018）。未設定時は
    services/edinetdb_config.build_edinetdb_adapter がこの例外で「キー未設定」を表す（ジョブは
    握って静かに skip＝ADR-064）。メッセージには対処（/settings でキー設定）を含める。
    """


def _to_float(value: Any) -> float | None:
    """数値化（None/非数値/NaN は None）。欠損を内部 None に倒す（捏造しない・ADR-014）。"""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # NaN 除去


def _normalize_financial(raw: dict[str, Any]) -> dict[str, Any]:
    """edinetdb.jp の財務 1 行を #2 が要る内部列名へ正規化する（ADR-010 の境界）。

    外部キー名（trade_receivables/inventories/...）→ 内部名（receivables/inventory/...）の対応を
    このアダプタに閉じる。cost_of_sales は近年の行に無いことがあるため、無ければ None を返し
    （services が revenue − gross_profit で補う）。欠損列はすべて None（捏造しない）。
    """
    fy = raw.get("fiscal_year")
    return {
        "fiscal_year": int(fy) if isinstance(fy, (int, float)) else None,
        "disclosed_date": raw.get("submit_date"),  # 'YYYY-MM-DD HH:MM' or None
        "accounting_standard": raw.get("accounting_standard"),
        "receivables": _to_float(raw.get("trade_receivables")),
        "inventory": _to_float(raw.get("inventories")),
        "revenue": _to_float(raw.get("revenue")),
        "gross_profit": _to_float(raw.get("gross_profit")),
        "cost_of_sales": _to_float(raw.get("cost_of_sales")),
    }


class EdinetDbAdapter:
    """edinetdb.jp API v1 のクライアント（会社解決 + 構造化財務・ADR-010/064）。

    状態（スロットル時刻・最終レート残量）はインスタンスに持つ（モジュールグローバル共有を避ける＝
    adapter 規約）。HTTP は httpx.Client（同期）＝呼び出し側の batch ジョブが同期 def なため。
    認証は `X-API-Key` ヘッダ（Client の default headers に載せる）。
    """

    def __init__(
        self,
        *,
        api_key: str,
        min_interval_seconds: float = 1.0,
        base_url: str = _BASE_URL,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._timeout = timeout
        self._throttle = Throttle(min_interval_seconds)
        self._last_budget: dict[str, int | None] = {}

    @property
    def last_budget(self) -> dict[str, int | None]:
        """直近レスポンスのレート残量（日次/月次の remaining/limit）。未取得なら空（ADR-064）。"""
        return dict(self._last_budget)

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self._base_url,
            timeout=self._timeout,
            headers={"X-API-Key": self._api_key},
        )

    def _get(self, client: httpx.Client, path: str, params: dict[str, Any] | None = None) -> Any:
        """1 リクエストを共通ヘルパで GET → JSON 化し、レート残量を捕捉する（ADR-010）。

        429/一時失敗は指数バックオフでリトライ。
        429 以外の 4xx/5xx とリトライ枯渇は EdinetDbAdapterError
        へ翻訳。edinetdb.jp は認証失敗を 4xx（status）で返すが、
        200＋`error`/`StatusCode` ボディの形にも
        備えてここで弾く（黙って空にしない＝ADR-018）。
        """
        resp = get_with_retry(
            client,
            path,
            params=params,
            throttle=self._throttle,
            on_http_error=lambda r: EdinetDbAdapterError(
                f"GET {path} が {r.status_code}: {r.text[:200]}"
                "（edinetdb.jp の X-API-Key 未設定/誤りの可能性。/settings で確認）"
            ),
            on_exhausted=lambda e: EdinetDbAdapterError(
                f"GET {path} が {DEFAULT_MAX_RETRIES} 回失敗しました（最後: {e}）。"
            ),
        )
        self._capture_budget(resp)
        try:
            payload = resp.json()
        except ValueError as exc:
            raise EdinetDbAdapterError(f"{path} の JSON 解析に失敗: {exc}") from exc
        if isinstance(payload, dict):
            err = payload.get("error")
            code = payload.get("StatusCode")
            if err or (code is not None and str(code) != "200"):
                raise EdinetDbAdapterError(
                    f"{path} が異常応答: {err or payload.get('message') or code}"
                )
        return payload

    def _capture_budget(self, resp: httpx.Response) -> None:
        """レスポンスヘッダからレート残量を取り出す（実予算 enforce 用・ADR-064）。"""

        def _int(key: str) -> int | None:
            v = resp.headers.get(key)
            return int(v) if v is not None and v.lstrip("-").isdigit() else None

        self._last_budget = {
            "daily_remaining": _int("x-ratelimit-remaining"),
            "daily_limit": _int("x-ratelimit-limit"),
            "monthly_remaining": _int("x-ratelimit-monthly-remaining"),
            "monthly_limit": _int("x-ratelimit-monthly-limit"),
        }

    def resolve_edinet_code(self, sec_code: str) -> str | None:
        """sec_code（5 桁・stocks.code 同形）から edinet_code を引く（/companies?q=・ADR-064）。

        q は sec_code でもピンポイントに効く（実機確認）。
        完全一致の sec_code 行の edinet_code を返す。
        見つからなければ None（edinetdb.jp に未収載＝呼び出し側が「未解決」として扱う）。
        """
        with self._client() as client:
            payload = self._get(client, "/companies", {"q": sec_code})
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            return None
        for row in data:
            if isinstance(row, dict) and str(row.get("sec_code")) == str(sec_code):
                ec = row.get("edinet_code")
                return str(ec) if ec else None
        return None

    def get_financials(self, edinet_code: str) -> list[dict[str, Any]]:
        """銘柄の財務時系列を内部列名に正規化して返す（古い順・ADR-064）。

        各行 = {fiscal_year, disclosed_date, accounting_standard, receivables, inventory, revenue,
        gross_profit, cost_of_sales}。近年の行のみ詳細 BS を持つ（古い年は None 多め）。
        #2 の DSO/DIO・
        YoY は services が直近 2 期の行を採って quant に渡す。
        """
        with self._client() as client:
            payload = self._get(client, f"/companies/{edinet_code}/financials")
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            return []
        return [_normalize_financial(r) for r in data if isinstance(r, dict)]

    def list_companies(self, *, page: int = 1, per_page: int = 50) -> dict[str, Any]:
        """会社一覧の 1 ページを素で返す（疎通テスト・任意の全件スイープ用・ADR-064）。

        戻り値は {data:[{sec_code, edinet_code, name_ja, ...}], meta:{pagination}}。
        """
        with self._client() as client:
            payload = self._get(client, "/companies", {"page": page, "per_page": per_page})
        return payload if isinstance(payload, dict) else {"data": [], "meta": {}}
