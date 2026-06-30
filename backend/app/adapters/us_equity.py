"""米国株アダプタ（UsEquityAdapter ＝ 複数ソースのフォールバック連鎖ファサード）。

ADR-010/039（米株拡張・UsEquityAdapter 新設）。米株は提示専用（Phase 7(B-1)）で、データ源は
yfinance 一本（当面）。IndexAdapter（adapters/index.py）と同型に、ソース別の小クラス
（UsEquitySource 実装）＋ファサード（UsEquityAdapter）で組む。ソースごとの取得・パース・
スロットル・「外部キー名→内部列名」の正規化は 1 クラスに閉じ込め、ファサードは関心ごと
（quotes / fundamentals）に別フォールバック連鎖を回すオーケストレータに徹する。

3 つの関心:
  - fetch_quotes(symbol, from_, to)   … 日足 OHLCV+adj_close を [{symbol,date,...}] で返す。
  - fetch_fundamentals(symbol)        … `.info` を内部列スナップショット dict に正規化する。
  - fetch_universe()                  … NASDAQ Trader directory から銘柄一覧を組む（ファサード直）。

フォールバック引き金: ソースが UsEquityAdapterError → 次ソース。ある関心に未対応のソースは
UsEquityNotSupported を投げ、ファサードが握って次ソースへ回す（全ソール未対応なら
UsEquityAdapterError）。yfinance の import は各ソースに閉じ込め、注入口（fetch 関数を __init__ で
差し替え可）でテストをネット非依存にする（testing-strategy・ADR-010）。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

import httpx

from app.adapters._http import Throttle
from app.config import settings

if TYPE_CHECKING:  # 型注釈専用（実行時 import を避け、テストのネット非依存を保つ）
    import pandas as pd

logger = logging.getLogger(__name__)


class UsEquityAdapterError(RuntimeError):
    """米株取得のエラー（HTTP 失敗・パースエラー・全ソース失敗等・ADR-010）。"""


class UsEquityNotSupported(RuntimeError):
    """このソースが要求された関心（quotes / fundamentals 等）に未対応である合図。

    ファサードはこれを握って次ソースへフォールバックする（エラーではなく「対応外」の表明）。
    """


def _first(d: dict[str, Any], keys: list[str], default: Any = None) -> Any:
    """候補キーのうち最初に存在し非 None な値を返す（外部キー名の揺れ吸収・ADR-010）。"""
    for k in keys:
        v = d.get(k)
        if v is not None:
            return v
    return default


def _norm_date(value: Any) -> str:
    """日付を 'YYYY-MM-DD' に正規化（'20230324' / '2023-03-24' のどちらでも）。"""
    s = str(value)
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return s


def _norm_yahoo_date(value: Any) -> str:
    """yfinance の index 値（Timestamp 等）を 'YYYY-MM-DD' に正規化する（ADR-010）。"""
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    return _norm_date(value)


def _to_float(value: Any) -> float | None:
    """数値化（NaN/None/非数値は None）。`.info` 欠損を内部 None に倒す（捏造しない・ADR-014）。"""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    # NaN（float("nan") != 自身）は None に倒す。
    if f != f:
        return None
    return f


class UsEquitySource(ABC):
    """米株ソースの共通インターフェース（1 クラス=1 ソース・ADR-010/039）。

    実装は symbol を受け取り、自ソースで取得して内部列名に正規化して返す。取得不能は
    UsEquityAdapterError を投げ（ファサードが次ソースへフォールバック）、その関心に未対応なら
    UsEquityNotSupported を投げる（ファサードが握って次ソースへ）。
    """

    name: str

    @abstractmethod
    def fetch_quotes(
        self, symbol: str, from_: str | None = None, to: str | None = None
    ) -> list[dict[str, Any]]:
        """日足 OHLCV+adj_close を [{symbol,date,open,high,low,close,volume,adj_close}] で返す。"""
        raise NotImplementedError

    @abstractmethod
    def fetch_fundamentals(self, symbol: str) -> dict[str, Any]:
        """`.info` 相当のスナップショットを内部列名の dict に正規化して返す。

        返す dict のキー: eps/bps/shares_net/dividend_per_share/net_sales/operating_profit/
        profit/gics_sector/industry/company_name/fin_disclosed_date（欠損は None）。
        加えて YoY 素（revenue_growth_yoy/earnings_growth_yoy）も拾えた範囲で含める（後続ウェーブ
        が採否を決める＝ADR-055・リスク1）。テーマタグの信号源として business_summary
        （事業説明テキスト・素のまま）も含める（ADR-050 段階A・欠損は None）。
        """
        raise NotImplementedError

    def fetch_quotes_bulk(
        self, symbols: list[str], from_: str | None = None, to: str | None = None
    ) -> dict[str, list[dict[str, Any]]]:
        """複数シンボルの日足を一括取得し {symbol: [行...]} で返す（バルク化・ADR-055）。

        基底デフォルトは既存 fetch_quotes を 1 件ずつ回す素朴版（バルク非対応ソースが将来連鎖に
        混ざっても動く保険・テスト内 fake source も無改変で済む）。個別 symbol の取得不能
        （UsEquityAdapterError）は握って他を巻き込まず、取れた symbol だけを dict に載せて返す
        （部分成功）。HTTP を束ねたいソース（Yahoo）はこれをオーバーライドする。
        """
        out: dict[str, list[dict[str, Any]]] = {}
        for s in symbols:
            try:
                out[s] = self.fetch_quotes(s, from_=from_, to=to)
            except UsEquityAdapterError as exc:
                logger.info("fetch_quotes_bulk(default): %s をスキップ: %s", s, exc)
        return out

    def fetch_balance_sheet(self, symbol: str) -> list[dict[str, Any]]:
        """年次の貸借対照表＋損益を #2 売掛/在庫の質用に正規化して返す（ADR-064 #2）。

        各行 = {fiscal_year, disclosed_date, receivables, inventory, revenue, gross_profit,
        cost_of_sales}（JP edinetdb の正規化と同形＝services.edinetdb_quality が共用）。基底は未対応
        （UsEquityNotSupported）＝対応ソース（Yahoo）がオーバーライドする。
        """
        raise UsEquityNotSupported(f"{getattr(self, 'name', '?')} は balance_sheet 未対応")


# ---------------------------------------------------------------------------
# Yahoo Finance ソース（yfinance）
# ---------------------------------------------------------------------------
_YAHOO_MIN_INTERVAL_SECONDS = 1.0  # yfinance はリクエスト間隔を軽くあける（ADR-010）

# yfinance.download の型（ticker, start, end → DataFrame か None）。テストで fake を注入する口。
YahooQuotesFetchFn = Callable[[str, str | None, str | None], "pd.DataFrame | None"]
# yfinance.download の複数ティッカー版（symbols, start, end → MultiIndex 列の DataFrame か None）。
# テストで fake を注入する口（バルク取得・ADR-055）。
YahooBulkQuotesFetchFn = Callable[[list[str], str | None, str | None], "pd.DataFrame | None"]
# yfinance.Ticker(symbol).info の型（ticker → dict）。テストで fake を注入する口。
YahooInfoFetchFn = Callable[[str], dict[str, Any]]
# yfinance の財務諸表取得の型（ticker → (balance_sheet, income_stmt) の DataFrame ペア）。
# テストで fake を注入する口（#2 売掛/在庫の質・ADR-064）。
YahooFinancialsFetchFn = Callable[[str], "tuple[pd.DataFrame | None, pd.DataFrame | None]"]


def _default_yahoo_quotes_fetch(
    source_symbol: str, start: str | None, end: str | None
) -> pd.DataFrame | None:
    """yfinance.download で 1 ティッカーの日足 OHLCV を取る既定 fetch（ADR-010/039）。

    auto_adjust=False で **素の OHLC ＋ 別列 'Adj Close'** の両方を得る（チャートは素の close を
    使い、配当・分割調整は adj_close 列で別に持つ＝日本株 daily_quotes 同型）。IndexAdapter は
    指数水準のため auto_adjust=True だが、米株は OHLCV と adj_close を分離保持したいので False。
    multi_level_index=False で単一ティッカーの列を 1 階層に平坦化する。yfinance の import はここに
    閉じ込め、テストは YahooQuotesFetchFn を注入してネットに出ない。
    """
    import yfinance as yf  # 遅延 import（テストのネット非依存・起動コスト回避）

    return yf.download(
        source_symbol,
        start=start,
        end=end,
        interval="1d",
        auto_adjust=False,
        actions=False,
        progress=False,
        threads=False,
        multi_level_index=False,
    )


def _default_yahoo_bulk_quotes_fetch(
    symbols: list[str], start: str | None, end: str | None
) -> pd.DataFrame | None:
    """yf.download で複数ティッカーの日足 OHLCV を一括取得する既定 fetch（ADR-010/039/055）。

    ADR-055 当初設計の「`yf.download` バッチ一括」。`group_by='ticker'` で columns を
    ('AAPL','Close') の 2 階層にし、`df['AAPL']` で単一ティッカーの OHLCV（単一階層）を切り出して
    既存 `_rows_from_df` をそのまま再利用できるようにする（per-symbol 取得＝約 3 時間が桁で短縮）。
    auto_adjust=False で素の OHLC＋別列 'Adj Close' を両取り（単数版 _default_yahoo_quotes_fetch と
    同方針）。multi_level_index=True で複数シンボルの 2 階層を保持する。yfinance の import はここに
    閉じ込め、テストは YahooBulkQuotesFetchFn を注入してネットに出ない。
    """
    import yfinance as yf  # 遅延 import（テストのネット非依存・起動コスト回避）

    return yf.download(
        symbols,
        start=start,
        end=end,
        interval="1d",
        auto_adjust=False,
        actions=False,
        progress=False,
        threads=False,
        group_by="ticker",
        multi_level_index=True,
    )


def _default_yahoo_info_fetch(source_symbol: str) -> dict[str, Any]:
    """yfinance.Ticker(symbol).info を取る既定 fetch（ADR-010/039）。

    `.info` は重い（1 銘柄ごとに HTTP）ので低頻度ローテ巡回で使う（ADR-033・後続ウェーブ）。
    yfinance の import はここに閉じ込め、テストは YahooInfoFetchFn を注入してネットに出ない。
    """
    import yfinance as yf  # 遅延 import

    info = yf.Ticker(source_symbol).info
    return dict(info) if info else {}


def _default_yahoo_financials_fetch(
    source_symbol: str,
) -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
    """yfinance の年次 balance_sheet / income_stmt を取る既定 fetch（#2・ADR-010/064）。

    重い（1 銘柄ごとに HTTP）ので watchlist/holdings の低頻度巡回でのみ使う。yfinance の import は
    ここに閉じ込め、テストは YahooFinancialsFetchFn を注入してネットに出ない。
    """
    import yfinance as yf  # 遅延 import

    t = yf.Ticker(source_symbol)
    # yfinance の stub は Series 可能性を含むが実行時は DataFrame。契約の型へ cast（ADR-010）。
    return (
        cast("pd.DataFrame | None", t.balance_sheet),
        cast("pd.DataFrame | None", t.income_stmt),
    )


def _df_get(df: pd.DataFrame | None, labels: list[str], col: Any) -> float | None:
    """DataFrame（index=項目名・columns=決算日）から候補ラベルのいずれかの値を取る（欠損 None）。"""
    if df is None or getattr(df, "empty", True):
        return None
    for label in labels:
        if label in df.index:
            try:
                return _to_float(df.loc[label, col])
            except (KeyError, TypeError, ValueError):
                continue
    return None


class YahooUsEquitySource(UsEquitySource):
    """Yahoo Finance（yfinance）ソース＝米株 OHLCV と `.info` を取る（ADR-010/039）。

    当面の唯一の米株ソース。`fetch_quotes` は素の OHLCV+adj_close、`fetch_fundamentals` は `.info`
    を内部列名に正規化する。`.info` のフィールド名は候補キーのフォールバック（_first）で吸収し、
    欠損は None に倒す（捏造しない＝ADR-014）。`fetch_quotes`/`fetch_info` 引数でテスト用 fake を
    注入できる（実 API・ネットに出さない＝testing-strategy）。
    """

    name = "yahoo"

    def __init__(
        self,
        fetch_quotes: YahooQuotesFetchFn | None = None,
        fetch_info: YahooInfoFetchFn | None = None,
        fetch_quotes_bulk: YahooBulkQuotesFetchFn | None = None,
        fetch_financials: YahooFinancialsFetchFn | None = None,
    ) -> None:
        self._fetch_quotes = fetch_quotes or _default_yahoo_quotes_fetch
        self._fetch_info = fetch_info or _default_yahoo_info_fetch
        self._bulk_fetch_quotes = fetch_quotes_bulk or _default_yahoo_bulk_quotes_fetch
        self._fetch_financials = fetch_financials or _default_yahoo_financials_fetch
        self._throttle = Throttle(
            settings.us_equity_min_interval_seconds or _YAHOO_MIN_INTERVAL_SECONDS
        )

    def fetch_quotes(
        self, symbol: str, from_: str | None = None, to: str | None = None
    ) -> list[dict[str, Any]]:
        """日足 OHLCV+adj_close を取得し内部列名に正規化して返す（ADR-010/039）。

        戻り値: [{symbol,date,open,high,low,close,volume,adj_close}, ...]。取得 0 行/失敗は
        UsEquityAdapterError を投げ、ファサードが次ソースへフォールバックできるようにする
        （ADR-018: 黙って 0 行にしない）。
        """
        self._throttle.wait()
        try:
            df = self._fetch_quotes(symbol, from_, to)
        except Exception as exc:  # noqa: BLE001 — 用途別の独自例外へ翻訳して次ソースへ回す
            raise UsEquityAdapterError(
                f"Yahoo（yfinance）symbol={symbol} の OHLCV 取得に失敗しました: {exc}"
            ) from exc

        if df is None or getattr(df, "empty", True):
            raise UsEquityAdapterError(
                f"Yahoo（yfinance）symbol={symbol} が 0 行を返しました"
                "（シンボル誤り/bot 制限/休場の疑い）。"
            )

        return self._rows_from_df(symbol, df)

    @staticmethod
    def _rows_from_df(symbol: str, df: pd.DataFrame) -> list[dict[str, Any]]:
        """OHLCV DataFrame を内部列名の行リストに変換する（MultiIndex 列も平坦化）。"""
        # 単一ティッカーでも稀に MultiIndex 列で返るため、最外列名（Open/Close 等）に平坦化する。
        if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
            df = df.copy()
            df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]

        rows: list[dict[str, Any]] = []
        for idx, rec in df.iterrows():
            d = {str(k): v for k, v in rec.items()}
            close = _to_float(_first(d, ["Close", "close"]))
            if close is None:  # close 欠損行（休場プレースホルダ等）は捨てる
                continue
            rows.append(
                {
                    "symbol": symbol,
                    "date": _norm_yahoo_date(idx),
                    "open": _to_float(_first(d, ["Open", "open"])),
                    "high": _to_float(_first(d, ["High", "high"])),
                    "low": _to_float(_first(d, ["Low", "low"])),
                    "close": close,
                    "volume": _to_float(_first(d, ["Volume", "volume"])),
                    "adj_close": _to_float(_first(d, ["Adj Close", "adj_close", "AdjClose"])),
                }
            )
        if not rows:
            raise UsEquityAdapterError(
                f"Yahoo（yfinance）symbol={symbol} は行はあるが有効な close が 0 件でした。"
            )
        return rows

    def fetch_quotes_bulk(
        self, symbols: list[str], from_: str | None = None, to: str | None = None
    ) -> dict[str, list[dict[str, Any]]]:
        """複数ティッカーの日足を 1 回の yf.download で一括取得する（ADR-010/039/055・バルク化）。

        ADR-055 当初設計の「`yf.download` バッチ一括」。`self._throttle.wait()` はバッチに 1 回だけ
        （symbol 数→バッチ数へスロットル削減）。返り値は {symbol: [行...]}＝取れた symbol だけ載せる
        （部分成功）。df 全体が空ならバッチ全滅として UsEquityAdapterError を raise（単数版の
        「0 行＝raise」と対称・ADR-018: 黙って 0 行にしない＝ファサードが次ソースへフォールバック
        できる）。個別 symbol の欠損（列が無い/有効 close 0 件）は dict から落とす（呼び出し側が
        「呼んだ symbol 数 vs 返った symbol 数」で欠損を数える）。
        """
        if not symbols:
            return {}
        self._throttle.wait()
        try:
            df = self._bulk_fetch_quotes(symbols, from_, to)
        except Exception as exc:  # noqa: BLE001 — 用途別の独自例外へ翻訳して次ソースへ回す
            raise UsEquityAdapterError(
                f"Yahoo（yfinance）バルク {len(symbols)} 銘柄の OHLCV 取得に失敗しました: {exc}"
            ) from exc

        if df is None or getattr(df, "empty", True):
            raise UsEquityAdapterError(
                f"Yahoo（yfinance）バルク {len(symbols)} 銘柄が 0 行を返しました"
                "（全滅＝bot 制限/休場/全シンボル誤りの疑い）。"
            )

        out: dict[str, list[dict[str, Any]]] = {}
        for symbol in symbols:
            sub = self._slice_symbol(df, symbol)
            if sub is None or getattr(sub, "empty", True):
                continue  # 応答に含まれない/空＝この symbol は欠損（呼び出し側が数える）
            try:
                out[symbol] = self._rows_from_df(symbol, sub)
            except UsEquityAdapterError:
                continue  # 有効 close 0 件の symbol も欠損扱いで drop
        return out

    @staticmethod
    def _slice_symbol(df: pd.DataFrame, symbol: str) -> pd.DataFrame | None:
        """バルク DataFrame から 1 ティッカー分の OHLCV を切り出す（group_by='ticker' 前提）。

        複数シンボルは columns が ('AAPL','Close') の 2 階層になる（実機確認済み）ため `df[symbol]`
        で単一階層に切り出す（symbol が最外 level に無ければ None＝欠損）。単一シンボル等で 1 階層で
        返った場合は df をそのまま返し、_rows_from_df の MultiIndex 平坦化に委ねる。
        """
        cols = df.columns
        if hasattr(cols, "nlevels") and cols.nlevels > 1:
            if symbol not in cols.get_level_values(0):
                return None
            return df[symbol]
        return df

    def fetch_fundamentals(self, symbol: str) -> dict[str, Any]:
        """`.info` を取得し内部列名のスナップショット dict に正規化して返す（ADR-010/014/048）。

        欠損は None に倒す（捏造しない）。operating_profit は `.info` に直接の項目が無いため
        operatingMargins × totalRevenue で**近似**する（近似である旨を明記＝ADR-014・リスク2）。
        YoY 素（revenue_growth_yoy/earnings_growth_yoy）は `.info` 提供の率をそのまま拾って渡す
        （採否は後続ウェーブ＝ADR-055・リスク1）。`fin_disclosed_date` は `.info` に決算開示日が
        無いため None（後続ウェーブで取得日等を充てる判断）。business_summary は
        `.info.longBusinessSummary` を素のまま渡す（既に短く compact 化不要＝ADR-050 段階A・
        テーマタグの信号源。欠損は None）。

        `.info` が空（yfinance の bot 検知/レート制限時は空 dict が返る）＝主要キー
        （company_name/eps/shares_net 等）が全欠損のときは UsEquityAdapterError を raise する
        （quotes の「0 行＝raise」と対称・ADR-018: 黙って欠損にしない・
        tasks/review-2026-06-12.md C-4。全 None の正常返却は呼び出し側の partial UPSERT で
        既存財務値を NULL 上書きしてしまう）。
        """
        self._throttle.wait()
        try:
            info = self._fetch_info(symbol)
        except Exception as exc:  # noqa: BLE001 — 用途別の独自例外へ翻訳して次ソースへ回す
            raise UsEquityAdapterError(
                f"Yahoo（yfinance）symbol={symbol} の `.info` 取得に失敗しました: {exc}"
            ) from exc

        info = info or {}
        net_sales = _to_float(_first(info, ["totalRevenue"]))
        operating_margin = _to_float(_first(info, ["operatingMargins"]))
        # operating_profit は `.info` 直接になく、営業利益率 × 売上の近似（ADR-014・リスク2）。
        operating_profit: float | None = None
        if operating_margin is not None and net_sales is not None:
            operating_profit = operating_margin * net_sales

        snapshot: dict[str, Any] = {
            "company_name": _first(info, ["longName", "shortName", "displayName"]),
            "gics_sector": _first(info, ["sector"]),
            "industry": _first(info, ["industry"]),
            "eps": _to_float(_first(info, ["trailingEps"])),
            "bps": _to_float(_first(info, ["bookValue"])),
            "shares_net": _to_float(_first(info, ["sharesOutstanding"])),
            "dividend_per_share": _to_float(_first(info, ["dividendRate"])),
            "net_sales": net_sales,
            "operating_profit": operating_profit,
            "profit": _to_float(_first(info, ["netIncomeToCommon"])),
            "fin_disclosed_date": None,  # `.info` に決算開示日なし（後続ウェーブで判断）
            # YoY 素（`.info` 提供の率・採否は後続ウェーブ＝ADR-055・リスク1）。
            "revenue_growth_yoy": _to_float(_first(info, ["revenueGrowth"])),
            "earnings_growth_yoy": _to_float(_first(info, ["earningsGrowth"])),
            # テーマタグの信号源（`.info.longBusinessSummary` 素のまま・ADR-050 段階A）。
            "business_summary": _first(info, ["longBusinessSummary"]),
        }
        # 主要キーが全欠損＝`.info` が空/bot 検知応答とみなして raise（quotes と契約対称・C-4）。
        # fin_disclosed_date は常に None のため判定から除外する。
        if all(v is None for k, v in snapshot.items() if k != "fin_disclosed_date"):
            raise UsEquityAdapterError(
                f"Yahoo（yfinance）symbol={symbol} の `.info` が空でした"
                "（bot 検知/レート制限の疑い・ADR-018: 黙って欠損にしない）。"
            )
        return snapshot

    def fetch_balance_sheet(self, symbol: str) -> list[dict[str, Any]]:
        """年次 balance_sheet＋income_stmt を #2 売掛/在庫の質用に正規化して返す（ADR-064 #2）。

        各行 = {fiscal_year, disclosed_date, receivables, inventory, revenue, gross_profit,
        cost_of_sales}（JP edinetdb と同形＝services.edinetdb_quality が共用）。決算日（DataFrame の
        列）ごとに 1 行。yfinance のフィールド名は候補キーで吸収し、欠損は None（捏造しない・
        ADR-014）。
        財務が空（bot 検知/未提供）なら UsEquityAdapterError（ファサードが次ソースへ）。
        """
        self._throttle.wait()
        try:
            bs, income = self._fetch_financials(symbol)
        except Exception as exc:  # noqa: BLE001 — 用途別の独自例外へ翻訳して次ソースへ回す
            raise UsEquityAdapterError(
                f"Yahoo（yfinance）symbol={symbol} の財務諸表取得に失敗しました: {exc}"
            ) from exc

        if bs is None or getattr(bs, "empty", True):
            raise UsEquityAdapterError(
                f"Yahoo（yfinance）symbol={symbol} の balance_sheet が空でした"
                "（bot 検知/未提供の疑い・ADR-018）。"
            )

        rows: list[dict[str, Any]] = []
        for col in bs.columns:
            fy = getattr(col, "year", None)
            rows.append(
                {
                    "fiscal_year": int(fy) if fy is not None else None,
                    "disclosed_date": _norm_yahoo_date(col),
                    "receivables": _df_get(bs, ["Receivables", "Accounts Receivable"], col),
                    "inventory": _df_get(bs, ["Inventory"], col),
                    "revenue": _df_get(income, ["Total Revenue", "Operating Revenue"], col),
                    "gross_profit": _df_get(income, ["Gross Profit"], col),
                    "cost_of_sales": _df_get(
                        income, ["Cost Of Revenue", "Reconciled Cost Of Revenue"], col
                    ),
                }
            )
        return rows


# ソース名 → クラスのレジストリ（settings.us_equity_source を解決）。今は yahoo のみ。
_REGISTRY: dict[str, type[UsEquitySource]] = {
    "yahoo": YahooUsEquitySource,
}


# ---------------------------------------------------------------------------
# NASDAQ Trader directory パーサ（ユニバース取得）
# ---------------------------------------------------------------------------
# nasdaqlisted.txt / otherlisted.txt はパイプ区切り・ヘッダ行付き・末尾に
# `File Creation Time: ...` のフッタ行が 1 行入る（パースで除く）。列構成が 2 ファイルで異なる:
#   nasdaqlisted.txt … Symbol|Security Name|Market Category|Test Issue|Financial Status|..|ETF|..
#   otherlisted.txt  … ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue
# 普通株のみ＝Test Issue=N かつ優先株/ユニット/権利/ワラントを Security Name で除外。ETF も行は
# 作るが is_etf=1（将来拡張用にフラグだけ保持＝grill 確定）。
_NASDAQLISTED_PATH = "/dynamic/SymDir/nasdaqlisted.txt"
_OTHERLISTED_PATH = "/dynamic/SymDir/otherlisted.txt"

# Security Name に含まれたら普通株でないと判断して除外する語（優先株/ユニット/権利/ワラント等）。
# 大小無視で部分一致。ETF は除外せず is_etf フラグで区別する（行は作る）。
_NON_COMMON_NAME_MARKERS: tuple[str, ...] = (
    "preferred",
    "depositary",
    "depository",
    "warrant",
    " right",
    "rights",
    " unit",
    "units",
    "notes",
    "debenture",
    "% ",  # 利率付き優先株（"5.00% ..." 等）
)


def _parse_directory(text: str, *, symbol_col: str) -> list[dict[str, Any]]:
    """NASDAQ Trader directory テキストをパースし [{symbol,company_name,is_etf}] を返す。

    パイプ区切り・1 行目ヘッダ・末尾の `File Creation Time` フッタ行を除く。Test Issue=Y の
    試験銘柄と、Security Name が優先株/ユニット/権利等の普通株でない銘柄を除外する。ETF は除外
    せず is_etf=1 で行を作る（将来拡張用フラグ・grill 確定）。symbol_col はファイルで異なる
    （nasdaqlisted="Symbol" / otherlisted="ACT Symbol"）。
    """
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []
    header = [h.strip() for h in lines[0].split("|")]
    idx = {name: i for i, name in enumerate(header)}
    if symbol_col not in idx:
        raise UsEquityAdapterError(
            f"NASDAQ Trader directory に列 '{symbol_col}' がありません: header={header}"
        )

    def _col(fields: list[str], name: str) -> str:
        i = idx.get(name)
        return fields[i].strip() if i is not None and i < len(fields) else ""

    rows: list[dict[str, Any]] = []
    for line in lines[1:]:
        # 末尾フッタ行（'File Creation Time: ...'）はパイプを含まず除外できる。
        if "|" not in line or line.lstrip().lower().startswith("file creation time"):
            continue
        fields = line.split("|")
        if len(fields) <= idx[symbol_col]:
            continue

        symbol = _col(fields, symbol_col)
        if not symbol:
            continue
        # 米国ティッカーに純数字（英字を一切含まない）は存在しない。過去に列ズレで日本株コード
        # （18330/18350）が us_stocks に混入した再発を入口で防ぐ（ADR-055 実装メモ）。
        if not any(c.isalpha() for c in symbol):
            continue
        # 試験銘柄は除外（Test Issue=Y）。
        if _col(fields, "Test Issue").upper() == "Y":
            continue

        name = _col(fields, "Security Name")
        is_etf = 1 if _col(fields, "ETF").upper() == "Y" else 0
        # 普通株でない（優先株/ユニット/権利等）は除外。ただし ETF はフラグで区別し残す。
        if is_etf == 0 and _is_non_common(name):
            continue

        rows.append({"symbol": symbol, "company_name": name or None, "is_etf": is_etf})
    return rows


def _is_non_common(security_name: str) -> bool:
    """Security Name から普通株でない（優先株/ユニット/権利等）かを判定する。"""
    low = security_name.lower()
    return any(marker in low for marker in _NON_COMMON_NAME_MARKERS)


# directory 取得関数の型（path → text）。テストで fake を注入する口（HTTP に出ない）。
DirectoryFetchFn = Callable[[str], str]


# ---------------------------------------------------------------------------
# ファサード（関心別フォールバック連鎖）
# ---------------------------------------------------------------------------
class UsEquityAdapter:
    """複数ソースをフォールバック連鎖する米株アダプタ（ADR-010/039・IndexAdapter 同型）。

    settings.us_equity_source（CSV・優先順）から _REGISTRY でソースを構築し、関心ごと
    （quotes / fundamentals）に別フォールバック連鎖を回す。ソースが UsEquityNotSupported なら
    握って次ソースへ、UsEquityAdapterError なら次ソースへ、全滅なら UsEquityAdapterError を raise。
    fetch_universe は NASDAQ Trader directory をファサードが直接組む（ソース非依存）。テストは
    sources= で UsEquitySource 群を、directory_fetch= で directory 取得関数を直接注入できる。
    """

    def __init__(
        self,
        sources: list[UsEquitySource] | None = None,
        directory_fetch: DirectoryFetchFn | None = None,
    ) -> None:
        self._sources = sources if sources is not None else self._build_from_config()
        self._directory_fetch = directory_fetch or self._default_directory_fetch

    @staticmethod
    def _build_from_config() -> list[UsEquitySource]:
        """settings.us_equity_source（優先順）を _REGISTRY で解決（未知名は warning で skip）。"""
        built: list[UsEquitySource] = []
        for name in settings.us_equity_source_list:
            cls = _REGISTRY.get(name)
            if cls is None:
                logger.warning("us_equity_source: 未知のソース名 '%s' をスキップします", name)
                continue
            built.append(cls())
        if not built:
            logger.warning("us_equity_source が空/全て未知です。yahoo にフォールバックします")
            built.append(YahooUsEquitySource())
        return built

    def fetch_quotes(
        self, symbol: str, from_: str | None = None, to: str | None = None
    ) -> list[dict[str, Any]]:
        """優先順にソースを試し、最初に成功した OHLCV を返す（関心=quotes・ADR-010）。

        UsEquityNotSupported は握って次ソースへ、その他例外も次ソースへ。全滅は
        UsEquityAdapterError。
        """
        return self._run_chain(
            "quotes", symbol, lambda src: src.fetch_quotes(symbol, from_=from_, to=to)
        )

    def fetch_quotes_bulk(
        self, symbols: list[str], from_: str | None = None, to: str | None = None
    ) -> dict[str, list[dict[str, Any]]]:
        """優先順にソースを試し、最初にバッチ取得成功したソースの結果を返す（バルク・ADR-010/055）。

        当面（yahoo 一本）は「あるソースがバッチ成功（部分結果含む）なら採用・打ち切り」で割り切る。
        欠損 symbol を次ソースで union 補完する per-symbol マージはしない（複数ソース化時の TODO）。
        全ソースがバッチ全滅（UsEquityAdapterError）なら _run_chain が集約して raise。
        """
        if not symbols:
            return {}
        return self._run_chain(
            "quotes(bulk)",
            f"[{len(symbols)} symbols]",
            lambda src: src.fetch_quotes_bulk(symbols, from_=from_, to=to),
        )

    def fetch_fundamentals(self, symbol: str) -> dict[str, Any]:
        """優先順にソースを試し最初に成功した `.info` を返す（関心=fundamentals・ADR-010）。"""
        return self._run_chain("fundamentals", symbol, lambda src: src.fetch_fundamentals(symbol))

    def fetch_balance_sheet(self, symbol: str) -> list[dict[str, Any]]:
        """優先順にソースを試し最初に成功した年次 BS+PL（#2 用）を返す（ADR-010/064）。"""
        return self._run_chain("balance_sheet", symbol, lambda src: src.fetch_balance_sheet(symbol))

    def _run_chain(self, concern: str, symbol: str, call: Callable[[UsEquitySource], Any]) -> Any:
        """関心ごとのフォールバック連鎖（UsEquityNotSupported は握って次・全滅で raise）。"""
        errors: list[str] = []
        for src in self._sources:
            try:
                return call(src)
            except UsEquityNotSupported as exc:
                logger.info("us source '%s' は %s 未対応→次ソースへ: %s", src.name, concern, exc)
                errors.append(f"{src.name}(未対応): {exc}")
            except Exception as exc:  # noqa: BLE001 — 次ソースへフォールバックするため握る
                logger.info(
                    "us source '%s' が %s symbol=%s で失敗→次ソースへ: %s",
                    src.name,
                    concern,
                    symbol,
                    exc,
                )
                errors.append(f"{src.name}: {exc}")
        raise UsEquityAdapterError(
            f"全ソースで {concern} symbol={symbol} の取得に失敗しました: {'; '.join(errors)}"
        )

    @staticmethod
    def _default_directory_fetch(path: str) -> str:
        """NASDAQ Trader directory（path）を HTTP GET でテキスト取得する（ADR-010）。"""
        base = settings.us_universe_base_url
        timeout = settings.us_equity_http_timeout_seconds
        with httpx.Client(base_url=base, timeout=timeout) as c:
            resp = c.get(path)
            if resp.status_code >= 400:
                raise UsEquityAdapterError(
                    f"NASDAQ Trader GET {path} が {resp.status_code}: {resp.text[:200]}"
                )
            return resp.text

    def fetch_universe(self) -> list[dict[str, Any]]:
        """NASDAQ Trader directory（nasdaqlisted ＋ otherlisted）から銘柄一覧を組む（ADR-010/039）。

        戻り値: [{symbol, company_name, is_etf}, ...]（普通株のみ＝Test Issue=N かつ優先株/ユニット/
        権利等を名称で除外。ETF も is_etf=1 で残す）。両ファイルを取得・パースし symbol 重複は
        先勝ちで排除する（nasdaqlisted を優先）。HTTP は _directory_fetch（注入可）で行う。
        """
        merged: dict[str, dict[str, Any]] = {}
        for path, symbol_col in (
            (_NASDAQLISTED_PATH, "Symbol"),
            (_OTHERLISTED_PATH, "ACT Symbol"),
        ):
            text = self._directory_fetch(path)
            for row in _parse_directory(text, symbol_col=symbol_col):
                merged.setdefault(row["symbol"], row)
        return list(merged.values())
