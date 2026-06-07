"""主要指数アダプタ（IndexAdapter ＝ 複数ソースのフォールバック連鎖ファサード）。

ADR-010: データソースはアダプタ越し。指数は複数ソースを優先順に試し、落ちたら次へ
フォールバックする（grill 2026-06）。ソースごとの取得・パース・スロットル・応答検証(ガード)は
1 クラス（IndexSource 実装）に閉じ込め、IndexAdapter はそれらを順に試すオーケストレータに徹する。

- フォールバック引き金: ソースが例外（IndexAdapterError 等）→ 次ソース。成功して 0 行
  （正規の空）は採用して打ち切り。全ソース例外なら IndexAdapter が IndexAdapterError を raise。
- canonical シンボル: 内部・index_quotes は canonical（^SPX/^NKX/^TPX）で統一。ソース別の表記差は
  各 IndexSource が内部で吸収し、返す行は canonical の symbol に戻す（透過フォールバック）。
- ソース選択: settings.index_sources（CSV・優先順）を _REGISTRY で解決して構築。

各 IndexSource は `fetch_index_quotes(symbol, from_, to) -> [{symbol,date,close}]` を実装し、
取得不能なら IndexAdapterError を投げる（＝ファサードが次ソースへ回す合図）。
外部キー名 → 内部列名の対応は各ソースクラスに閉じ込める（ADR-010）。
"""

from __future__ import annotations

import csv
import io
import logging
import time
from abc import ABC, abstractmethod
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class IndexAdapterError(RuntimeError):
    """指数取得のエラー（HTTP 失敗・パースエラー・全ソース失敗等）。"""


def _norm_date(value: Any) -> str:
    """日付を 'YYYY-MM-DD' に正規化（'20230324' / '2023-03-24' のどちらでも）。"""
    s = str(value)
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return s


class IndexSource(ABC):
    """指数ソースの共通インターフェース（1 クラス=1 ソース・ADR-010）。

    実装は canonical シンボル（^SPX/^NKX/^TPX）を受け取り、自ソースの表記へ変換して取得し、
    返す行の `symbol` は canonical に戻す（フォールバック透過のため）。取得不能は
    IndexAdapterError を投げる（IndexAdapter が次ソースへフォールバックする合図）。
    """

    name: str

    @abstractmethod
    def fetch_index_quotes(
        self, symbol: str, from_: str | None = None, to: str | None = None
    ) -> list[dict[str, Any]]:
        """canonical symbol の日次終値を [{symbol, date, close}] で返す（symbol は canonical）。"""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Stooq ソース（CSV）
# ---------------------------------------------------------------------------
_STOOQ_BASE_URL = "https://stooq.com"
_STOOQ_MIN_INTERVAL_SECONDS = 1.0  # Stooq はリクエスト間隔を 1 秒程度あければ十分
_STOOQ_MAX_RETRIES = 3
_STOOQ_RETRY_BASE_SLEEP = 2.0


class StooqIndexSource(IndexSource):
    """Stooq（CSV）ソース（spec §3.1・裁定 L-10）。

    Stooq CSV 形式: `https://stooq.com/q/d/l/?s=<symbol>&i=d`
    CSV ヘッダ: Date,Open,High,Low,Close,Volume（終値のみ使用）。
    canonical シンボル（^SPX/^NKX/^TPX）は Stooq 表記と同一（恒等変換）。
    `client` 引数でテスト用のスタブを注入できる。
    """

    name = "stooq"

    def __init__(
        self,
        base_url: str = _STOOQ_BASE_URL,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url
        self._client = client  # テスト注入用（None なら都度作成）
        self._last_request_ts = 0.0  # スロットル用（monotonic 時刻）
        # スロットル間隔は設定から読む（Stooq は 1.0 で十分・ADR-010）。
        # モジュール定数 _STOOQ_MIN_INTERVAL_SECONDS はフォールバック既定。
        self._min_interval = settings.index_min_interval_seconds or _STOOQ_MIN_INTERVAL_SECONDS

    @staticmethod
    def _to_source_symbol(symbol: str) -> str:
        """canonical シンボル → Stooq シンボル。現状は恒等（将来ズレたらここで対応）。"""
        return symbol

    def _throttle(self) -> None:
        """前回リクエストから最低 self._min_interval あける。"""
        wait = self._min_interval - (time.monotonic() - self._last_request_ts)
        if wait > 0:
            time.sleep(wait)
        self._last_request_ts = time.monotonic()

    def _get_csv(self, source_symbol: str, from_: str | None = None, to: str | None = None) -> str:
        """Stooq から CSV テキストを取得する（spec §3.1）。HTTP 失敗は IndexAdapterError に変換。"""
        params: dict[str, str] = {"s": source_symbol, "i": "d"}
        if from_:
            params["d1"] = from_.replace("-", "")
        if to:
            params["d2"] = to.replace("-", "")

        def do_request(c: httpx.Client) -> str:
            for attempt in range(_STOOQ_MAX_RETRIES):
                self._throttle()
                resp = c.get("/q/d/l/", params=params)
                if resp.status_code == 429:
                    time.sleep(_STOOQ_RETRY_BASE_SLEEP * (2**attempt))
                    continue
                if resp.status_code >= 400:
                    raise IndexAdapterError(
                        f"Stooq GET /q/d/l/ symbol={source_symbol} が"
                        f" {resp.status_code}: {resp.text[:200]}"
                    )
                return resp.text
            raise IndexAdapterError(
                f"Stooq GET /q/d/l/ symbol={source_symbol} がレート制限で"
                f" {_STOOQ_MAX_RETRIES} 回失敗しました。"
            )

        if self._client is not None:
            text = do_request(self._client)
        else:
            with httpx.Client(base_url=self._base_url, timeout=30.0) as c:
                text = do_request(c)

        self._ensure_csv(source_symbol, text)
        return text

    @staticmethod
    def _ensure_csv(source_symbol: str, text: str) -> None:
        """応答が CSV か検証する（ADR-018/038: 黙って 0 行にしない・Stooq 固有）。

        Stooq は bot よけの JS チャレンジ HTML やレート制限メッセージ（"Exceeded ..."）を
        HTTP 200 で返すことがある。その本文を CSV としてパースすると無言で 0 行になり、
        ジョブが ok=True のまま「静かな失敗」になる（2026-06 に index 全シンボル 0 行で露見）。
        CSV ヘッダ（`Date,`）で始まらなければ IndexAdapterError を投げ、IndexAdapter に次ソースへ
        フォールバックさせる（全滅ならジョブ ok=False → Discord）。
        ヘッダのみ（データ 0 行）は正規の空応答なので通す。
        """
        first_line = text.lstrip().splitlines()[0].lower() if text.strip() else ""
        if not first_line.startswith("date,"):
            snippet = " ".join(text.split())[:200]
            raise IndexAdapterError(
                f"Stooq symbol={source_symbol} が CSV を返しませんでした"
                f"（bot よけ/レート制限の疑い）: {snippet or '<空応答>'}"
            )

    def fetch_index_quotes(
        self, symbol: str, from_: str | None = None, to: str | None = None
    ) -> list[dict[str, Any]]:
        """canonical symbol の日次終値を取得し、内部列名に正規化して返す（spec §3.1）。

        戻り値: [{"symbol"(canonical), "date", "close"}, ...] の list。
        CSV ヘッダ: Date,Open,High,Low,Close,Volume（Close のみ使用）。
        空行・ヘッダ欠落・No data 行はスキップする。
        """
        source_symbol = self._to_source_symbol(symbol)
        text = self._get_csv(source_symbol, from_=from_, to=to)
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
                    "symbol": symbol,  # canonical に戻す（透過フォールバック）
                    "date": _norm_date(date_val),
                    "close": close,
                }
            )
        return rows


# ソース名 → クラスのレジストリ（settings.index_sources の名前を解決）。
# 実ソース（yahoo/jquants 等）を足すときはここに登録する。
_REGISTRY: dict[str, type[IndexSource]] = {
    "stooq": StooqIndexSource,
}


# ---------------------------------------------------------------------------
# ファサード（フォールバック連鎖）
# ---------------------------------------------------------------------------
class IndexAdapter:
    """複数ソースをフォールバック連鎖する指数アダプタ（ADR-010・名前維持＝呼び出し側無改修）。

    settings.index_sources（CSV・優先順）から _REGISTRY でソースを構築し、各シンボルを順に試す。
    ソースが例外なら次へ、成功（0 行含む）したら即採用、全滅なら IndexAdapterError を raise。
    テストは sources= で IndexSource 群を直接注入できる。
    """

    def __init__(self, sources: list[IndexSource] | None = None) -> None:
        self._sources = sources if sources is not None else self._build_from_config()

    @staticmethod
    def _build_from_config() -> list[IndexSource]:
        """settings.index_sources（優先順）を _REGISTRY で解決する。未知名は warning でスキップ。"""
        built: list[IndexSource] = []
        for name in settings.index_source_list:
            cls = _REGISTRY.get(name)
            if cls is None:
                logger.warning("index_sources: 未知のソース名 '%s' をスキップします", name)
                continue
            built.append(cls())
        if not built:
            logger.warning("index_sources が空/全て未知です。stooq にフォールバックします")
            built.append(StooqIndexSource())
        return built

    def fetch_index_quotes(
        self, symbol: str, from_: str | None = None, to: str | None = None
    ) -> list[dict[str, Any]]:
        """優先順にソースを試し、最初に成功した結果（0 行含む）を返す（grill 2026-06）。

        ソースが例外を投げたら次ソースへフォールバック。全ソース失敗なら IndexAdapterError。
        """
        errors: list[str] = []
        for src in self._sources:
            try:
                return src.fetch_index_quotes(symbol, from_=from_, to=to)
            except Exception as exc:  # noqa: BLE001 — 次ソースへフォールバックするため握る
                logger.info(
                    "index source '%s' が symbol=%s で失敗→次ソースへ: %s", src.name, symbol, exc
                )
                errors.append(f"{src.name}: {exc}")
        raise IndexAdapterError(
            f"全ソースで symbol={symbol} の指数取得に失敗しました: {'; '.join(errors)}"
        )
