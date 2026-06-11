"""FX アダプタ（FxAdapter ＝ 複数ソースのフォールバック連鎖ファサード）。

ADR-010/057（Phase 7(B-2) FX/保有波及）。米株保有を JPY 資産概要へ合算するための為替レートを
取得する。データ源は yfinance JPY=X 日足終値一本（当面）。UsEquityAdapter（adapters/us_equity.py）と
同型に、ソース別の小クラス（FxSource 実装）＋ファサード（FxAdapter）で組むが、関心は 1 つだけ
（fetch_rates）なので連鎖はそれに閉じる。

唯一の関心:
  - fetch_rates(pair, from_, to) … 日足 FX 終値を [{date, pair, rate}] で返す（rate は JPY/USD）。

フォールバック引き金: ソースが FxAdapterError → 次ソース。全ソース失敗なら FxAdapterError。
yfinance の import は各ソースに閉じ込め、注入口（fetch 関数を __init__ で差し替え可）でテストを
ネット非依存にする（testing-strategy・ADR-010）。
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from app.config import settings

if TYPE_CHECKING:  # 型注釈専用（実行時 import を避け、テストのネット非依存を保つ）
    import pandas as pd

logger = logging.getLogger(__name__)

# 通貨ペア → yfinance ティッカーの対応（USDJPY ＝ JPY=X＝1 USD あたりの JPY）。
# 当面 USDJPY のみ。別ペア追加時はここに足す（直結ハードコードを 1 か所に閉じ込める・ADR-010）。
_PAIR_TO_YAHOO_TICKER: dict[str, str] = {
    "USDJPY": "JPY=X",
}


class FxAdapterError(RuntimeError):
    """FX レート取得のエラー（HTTP 失敗・パースエラー・全ソース失敗等・ADR-010）。"""


def _norm_yahoo_date(value: Any) -> str:
    """yfinance の index 値（Timestamp 等）を 'YYYY-MM-DD' に正規化（us_equity 同型・ADR-010）。"""
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    return str(value)


def _to_float(value: Any) -> float | None:
    """数値化（NaN/None/非数値は None）。欠損を内部 None に倒す（捏造しない・ADR-014）。"""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


class FxSource(ABC):
    """FX ソースの共通インターフェース（1 クラス=1 ソース・ADR-010/057）。"""

    name: str

    @abstractmethod
    def fetch_rates(
        self, pair: str, from_: str | None = None, to: str | None = None
    ) -> list[dict[str, Any]]:
        """日足 FX 終値を [{date, pair, rate}] で返す（rate は JPY/USD）。"""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Yahoo Finance ソース（yfinance JPY=X）
# ---------------------------------------------------------------------------
_YAHOO_MIN_INTERVAL_SECONDS = 1.0  # yfinance はリクエスト間隔を軽くあける（ADR-010）

# yfinance.download の型（ticker, start, end → DataFrame か None）。テストで fake を注入する口。
YahooFxFetchFn = Callable[[str, str | None, str | None], "pd.DataFrame | None"]


def _default_yahoo_fx_fetch(
    source_ticker: str, start: str | None, end: str | None
) -> pd.DataFrame | None:
    """yfinance.download で FX 日足終値を取る既定 fetch（ADR-010/057）。

    auto_adjust=True で終値（Close）水準を得る（FX は配当・分割がなく adj 不要＝IndexAdapter
    同型）。
    multi_level_index=False で単一ティッカーの列を 1 階層に平坦化する。yfinance の import はここに
    閉じ込め、テストは YahooFxFetchFn を注入してネットに出ない。
    """
    import yfinance as yf  # 遅延 import（テストのネット非依存・起動コスト回避）

    return yf.download(
        source_ticker,
        start=start,
        end=end,
        interval="1d",
        auto_adjust=True,
        actions=False,
        progress=False,
        threads=False,
        multi_level_index=False,
    )


class YahooFxSource(FxSource):
    """Yahoo Finance（yfinance）ソース＝FX 日足終値を取る（ADR-010/057）。

    当面の唯一の FX ソース。pair を yfinance ティッカー（USDJPY→JPY=X）に解決して終値を取り、
    [{date, pair, rate}] に正規化する。取得不能・0 行は FxAdapterError を投げ（ファサードが次へ
    フォールバック・ADR-018: 黙って 0 行にしない）。`fetch` 引数でテスト用 fake を注入できる。
    """

    name = "yahoo"

    def __init__(self, fetch: YahooFxFetchFn | None = None) -> None:
        self._fetch = fetch or _default_yahoo_fx_fetch
        self._last_request_ts = 0.0  # スロットル用（monotonic 時刻）
        self._min_interval = settings.fx_min_interval_seconds or _YAHOO_MIN_INTERVAL_SECONDS

    def _throttle(self) -> None:
        """前回リクエストから最低 self._min_interval あける（us_equity に倣う・ADR-010）。"""
        wait = self._min_interval - (time.monotonic() - self._last_request_ts)
        if wait > 0:
            time.sleep(wait)
        self._last_request_ts = time.monotonic()

    def fetch_rates(
        self, pair: str, from_: str | None = None, to: str | None = None
    ) -> list[dict[str, Any]]:
        """FX 日足終値を取得し [{date, pair, rate}] に正規化して返す（ADR-010/057）。"""
        ticker = _PAIR_TO_YAHOO_TICKER.get(pair)
        if ticker is None:
            raise FxAdapterError(f"未知の通貨ペア '{pair}'（_PAIR_TO_YAHOO_TICKER 未定義）")

        self._throttle()
        try:
            df = self._fetch(ticker, from_, to)
        except Exception as exc:  # noqa: BLE001 — 用途別の独自例外へ翻訳して次ソースへ回す
            raise FxAdapterError(
                f"Yahoo（yfinance）pair={pair}（{ticker}）の FX 取得に失敗しました: {exc}"
            ) from exc

        if df is None or getattr(df, "empty", True):
            raise FxAdapterError(
                f"Yahoo（yfinance）pair={pair}（{ticker}）が 0 行を返しました"
                "（ティッカー誤り/bot 制限/休場の疑い）。"
            )

        return self._rows_from_df(pair, df)

    @staticmethod
    def _rows_from_df(pair: str, df: pd.DataFrame) -> list[dict[str, Any]]:
        """FX 終値 DataFrame を [{date, pair, rate}] に変換する（MultiIndex 列も平坦化）。"""
        if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
            df = df.copy()
            df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]

        rows: list[dict[str, Any]] = []
        for idx, rec in df.iterrows():
            d = {str(k): v for k, v in rec.items()}
            rate = _to_float(d.get("Close") if "Close" in d else d.get("close"))
            if rate is None:  # close 欠損行（休場プレースホルダ等）は捨てる
                continue
            rows.append({"date": _norm_yahoo_date(idx), "pair": pair, "rate": rate})

        if not rows:
            raise FxAdapterError(
                f"Yahoo（yfinance）pair={pair} は行はあるが有効な close が 0 件でした。"
            )
        return rows


# ソース名 → クラスのレジストリ（settings.fx_source を解決）。今は yahoo のみ。
_REGISTRY: dict[str, type[FxSource]] = {
    "yahoo": YahooFxSource,
}


# ---------------------------------------------------------------------------
# ファサード（フォールバック連鎖）
# ---------------------------------------------------------------------------
class FxAdapter:
    """複数ソースをフォールバック連鎖する FX アダプタ（ADR-010/057・UsEquityAdapter 同型）。

    settings.fx_source（CSV・優先順）から _REGISTRY でソースを構築し、fetch_rates をフォールバック
    連鎖で回す。ソースが FxAdapterError なら次ソースへ、全滅なら FxAdapterError を raise。テストは
    sources= で FxSource 群を直接注入できる。
    """

    def __init__(self, sources: list[FxSource] | None = None) -> None:
        self._sources = sources if sources is not None else self._build_from_config()

    @staticmethod
    def _build_from_config() -> list[FxSource]:
        """settings.fx_source（優先順）を _REGISTRY で解決（未知名は warning で skip）。"""
        built: list[FxSource] = []
        for name in settings.fx_source_list:
            cls = _REGISTRY.get(name)
            if cls is None:
                logger.warning("fx_source: 未知のソース名 '%s' をスキップします", name)
                continue
            built.append(cls())
        if not built:
            logger.warning("fx_source が空/全て未知です。yahoo にフォールバックします")
            built.append(YahooFxSource())
        return built

    def fetch_rates(
        self, pair: str = "USDJPY", from_: str | None = None, to: str | None = None
    ) -> list[dict[str, Any]]:
        """優先順にソースを試し、最初に成功した FX 終値を返す（ADR-010）。

        全ソース失敗で FxAdapterError。戻り値: [{date, pair, rate}, ...]（date 昇順は呼び出し側で
        保証不要＝yfinance は昇順で返す）。
        """
        errors: list[str] = []
        for src in self._sources:
            try:
                return src.fetch_rates(pair, from_=from_, to=to)
            except Exception as exc:  # noqa: BLE001 — 次ソースへフォールバックするため握る
                logger.info("fx source '%s' が pair=%s で失敗→次ソースへ: %s", src.name, pair, exc)
                errors.append(f"{src.name}: {exc}")
        raise FxAdapterError(
            f"全ソースで pair={pair} の FX 取得に失敗しました: {'; '.join(errors)}"
        )
