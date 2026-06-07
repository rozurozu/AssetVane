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
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import httpx

from app.config import settings

if TYPE_CHECKING:  # 型注釈専用（実行時 import を避け、テストのネット非依存を保つ）
    import pandas as pd

logger = logging.getLogger(__name__)


class IndexAdapterError(RuntimeError):
    """指数取得のエラー（HTTP 失敗・パースエラー・全ソース失敗等）。"""


# 米国 SPDR 業種 ETF（GICS 11 セクター・Phase 7 リードラグ用・ADR-010）。
# canonical シンボル＝素ティッカー（Yahoo でもそのまま。Stooq は ".us" を付ける表記差を吸収）。
# fetch_index ジョブがこの定数を index_symbols に足して index_quotes へ取り込む。
US_SECTOR_ETFS: tuple[str, ...] = (
    "XLB",  # Materials（素材）
    "XLE",  # Energy（エネルギー）
    "XLF",  # Financials（金融）
    "XLI",  # Industrials（資本財）
    "XLK",  # Information Technology（情報技術）
    "XLP",  # Consumer Staples（生活必需品）
    "XLU",  # Utilities（公益）
    "XLV",  # Health Care（ヘルスケア）
    "XLY",  # Consumer Discretionary（一般消費財）
    "XLC",  # Communication Services（通信サービス）
    "XLRE",  # Real Estate（不動産）
)


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


# ---------------------------------------------------------------------------
# Yahoo Finance ソース（yfinance）
# ---------------------------------------------------------------------------
_YAHOO_MIN_INTERVAL_SECONDS = 1.0  # yfinance はリクエスト間隔を軽くあける（ADR-010）

# canonical シンボル → Yahoo Finance シンボルの対応（指数のみ。ETF・米個別は恒等）。
# Yahoo の指数表記は Stooq と異なるためここで吸収する（フォールバック透過・ADR-010）。
# yfinance で各シンボルの実在を確認済み（2026-06）:
# - ^SPX（S&P500）→ ^GSPC（Yahoo の S&P500 シンボル・取得 OK）
# - ^NKX（日経225）→ ^N225（Yahoo の日経平均シンボル・取得 OK）
# - ^TPX（TOPIX）: TOPIX は J-Quants /v2/indices/bars/daily/topix（JQuantsIndexSource・Light 以上）
#   で取得する。Free では 403 で取れず yahoo/stooq も TOPIX を返さないため当面失敗する。
#   Yahoo で TOPIX 指数の有効シンボルは特定できなかった（^TPX/^TOPX/998405.T/^TPX.T いずれも
#   0 行 or 404・2026-06 検証）ので恒等で渡し、yahoo→stooq→jquants の順に落ちる。
_YAHOO_INDEX_SYMBOLS: dict[str, str] = {
    "^SPX": "^GSPC",
    "^NKX": "^N225",
    # "^TPX": <未確定>,  # 上記 TODO 参照（恒等で渡し Stooq フォールバックに委ねる）
}

# yfinance の取得関数の型（ticker, start, end → DataFrame か None）。テストで fake を注入する口。
# yfinance.download は取得失敗時に None を返しうるため None も許容する（呼び出し側でガード）。
YahooFetchFn = Callable[[str, str | None, str | None], "pd.DataFrame | None"]


def _default_yahoo_fetch(
    source_symbol: str, start: str | None, end: str | None
) -> pd.DataFrame | None:
    """yfinance.download で 1 ティッカーの日足を取る既定 fetch（ADR-010）。

    auto_adjust=True で配当・分割調整後 OHLC を得る（ETF の分配金調整が要るため）。
    multi_level_index=False で単一ティッカーの列を 1 階層（Close 等）に平坦化する。
    yfinance の import はここに閉じ込め、テストは YahooFetchFn を注入してネットに出ない。
    """
    import yfinance as yf  # 遅延 import（テストのネット非依存・起動コスト回避）

    # yfinance の end は排他的。to を含めたいので end の翌日を渡したいが、呼び出し側は
    # 「to まで含む」契約。yfinance に YYYY-MM-DD を渡すと end は exclusive のため、
    # ここでは start/end をそのまま渡し、end が None なら最新まで取得する。
    # （差分取得の重複は repo の UPSERT が冪等に吸収するため厳密一致は不要・ADR-002）
    return yf.download(
        source_symbol,
        start=start,
        end=end,
        interval="1d",
        auto_adjust=True,
        actions=False,
        progress=False,
        threads=False,
        multi_level_index=False,
    )


class YahooIndexSource(IndexSource):
    """Yahoo Finance（yfinance）ソース。指数・米国業種 ETF の配当調整後 close を取る（ADR-010）。

    Stooq が bot 判定で死んだときの主ソース（grill 2026-06）。canonical シンボルを Yahoo 表記へ
    変換して取得し（指数は _YAHOO_INDEX_SYMBOLS・ETF/米個別は恒等）、返す行の symbol は
    canonical に戻す（フォールバック透過）。auto_adjust=True で ETF の分配金・株式分割を
    調整した close を使う。取得 0 行/失敗は IndexAdapterError を投げ、ファサードが次ソース
    （Stooq）へ回せるようにする（ADR-018/038: 黙って 0 行にしない）。`fetch` 引数で
    テスト用 fake を注入できる。
    """

    name = "yahoo"

    def __init__(self, fetch: YahooFetchFn | None = None) -> None:
        self._fetch = fetch or _default_yahoo_fetch  # テスト注入用（None なら yfinance を使う）
        self._last_request_ts = 0.0  # スロットル用（monotonic 時刻）
        # スロットル間隔は設定から読む（無ければ既定）。Stooq と同じ
        # index_min_interval_seconds を流用する。
        self._min_interval = settings.index_min_interval_seconds or _YAHOO_MIN_INTERVAL_SECONDS

    @staticmethod
    def _to_source_symbol(symbol: str) -> str:
        """canonical シンボル → Yahoo シンボル。

        指数は _YAHOO_INDEX_SYMBOLS で変換（^SPX→^GSPC・^NKX→^N225・^TPX→恒等）。
        米国 ETF（XLK 等）・米個別は素ティッカーが Yahoo シンボルと同一なので恒等で通す。
        未知の `^`始まり（指数表記）は変換表に無ければそのまま渡す（TODO: 必要なら追記）。
        """
        return _YAHOO_INDEX_SYMBOLS.get(symbol, symbol)

    def _throttle(self) -> None:
        """前回リクエストから最低 self._min_interval あける（StooqIndexSource に倣う）。"""
        wait = self._min_interval - (time.monotonic() - self._last_request_ts)
        if wait > 0:
            time.sleep(wait)
        self._last_request_ts = time.monotonic()

    def fetch_index_quotes(
        self, symbol: str, from_: str | None = None, to: str | None = None
    ) -> list[dict[str, Any]]:
        """canonical symbol の日次調整後 close を取得し内部列名に正規化して返す（ADR-010）。

        戻り値: [{"symbol"(canonical), "date", "close"}, ...] の list。
        DataFrame の index（日付）と Close 列のみ使用。取得 0 行/失敗は IndexAdapterError を投げ、
        ファサードが次ソースへフォールバックできるようにする（ADR-018/038: 黙って 0 行にしない）。
        """
        source_symbol = self._to_source_symbol(symbol)
        self._throttle()
        try:
            df = self._fetch(source_symbol, from_, to)
        except Exception as exc:  # noqa: BLE001 — 用途別の独自例外へ翻訳して次ソースへ回す
            raise IndexAdapterError(
                f"Yahoo（yfinance）symbol={source_symbol} の取得に失敗しました: {exc}"
            ) from exc

        if df is None or getattr(df, "empty", True):
            raise IndexAdapterError(
                f"Yahoo（yfinance）symbol={source_symbol} が 0 行を返しました"
                "（シンボル誤り/bot 制限/休場の疑い）。次ソースへフォールバックします。"
            )

        # 単一ティッカーでも稀に MultiIndex 列で返ることがあるため Close を頑健に取り出す。
        close_series = self._extract_close(df, source_symbol)

        rows: list[dict[str, Any]] = []
        for idx, value in close_series.items():
            if value is None:
                continue
            try:
                close = float(value)
            except (TypeError, ValueError):
                continue
            # NaN（float("nan") != 自身）はスキップ。
            if close != close:
                continue
            rows.append(
                {
                    "symbol": symbol,  # canonical に戻す（透過フォールバック）
                    "date": _norm_yahoo_date(idx),
                    "close": close,
                }
            )

        if not rows:
            raise IndexAdapterError(
                f"Yahoo（yfinance）symbol={source_symbol} は行はあるが有効な close が 0 件でした。"
            )
        return rows

    @staticmethod
    def _extract_close(df: pd.DataFrame, source_symbol: str) -> pd.Series:
        """DataFrame から Close 列（Series）を取り出す（単一/MultiIndex 列の両対応）。"""
        import pandas as pd  # 遅延 import（実行時のみ・テストのネット非依存を保つ）

        columns = df.columns
        if "Close" not in columns and not (hasattr(columns, "nlevels") and columns.nlevels > 1):
            raise IndexAdapterError(
                f"Yahoo（yfinance）symbol={source_symbol} に Close 列がありません。"
            )
        try:
            close = df["Close"]
        except KeyError as exc:  # MultiIndex で 'Close' 階層が無いケース
            raise IndexAdapterError(
                f"Yahoo（yfinance）symbol={source_symbol} に Close 列がありません。"
            ) from exc
        # df["Close"] が DataFrame（MultiIndex／複数ティッカー）なら最初の列を Series 化する。
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        return pd.Series(close)


def _norm_yahoo_date(value: Any) -> str:
    """yfinance の index 値（Timestamp 等）を 'YYYY-MM-DD' に正規化する（ADR-010）。"""
    # pandas.Timestamp は strftime を持つ。それ以外（str 等）は _norm_date に委ねる。
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    return _norm_date(value)


# ---------------------------------------------------------------------------
# J-Quants ソース（TOPIX 専用・最後段）
# ---------------------------------------------------------------------------
# JQuantsAdapter.fetch_topix(from_, to) -> [{date, close}] の型（テストで fake を注入する口）。
JQuantsTopixFetchFn = Callable[[str | None, str | None], list[dict[str, Any]]]


class JQuantsIndexSource(IndexSource):
    """J-Quants（/v2/indices/bars/daily/topix）ソース。**TOPIX（^TPX）専用**（ADR-008/010）。

    J-Quants は日本株専用で ^SPX/^NKX 等の海外・日経指数を持たない（docs/jquants.md）。そのため
    ^TPX 以外のシンボルは IndexAdapterError を投げ、ファサードが次ソースへフォールバックできる
    ようにする（^SPX/^NKX は前段の yahoo が成功で返すのでここには到達しない）。^TPX は
    JQuantsAdapter.fetch_topix を呼び canonical の symbol を付け直して返す。

    TOPIX は **Light 以上**でのみ取得可（Free は 403 ＝ JQuantsError がそのまま伝播し、ファサードの
    except が握って次へ落とす。yahoo/stooq も TOPIX は取れないため Free では当面失敗する）。
    `fetch_topix` 引数でテスト用 fake を注入できる（実 API・ネットに出さない＝testing-strategy）。
    """

    name = "jquants"

    def __init__(self, fetch_topix: JQuantsTopixFetchFn | None = None) -> None:
        # テスト注入用（None なら JQuantsAdapter を遅延生成して fetch_topix を呼ぶ）。
        # JQuantsAdapter の生成は API キーを要するため、注入が無いときだけ実行時に作る。
        self._fetch_topix = fetch_topix

    def fetch_index_quotes(
        self, symbol: str, from_: str | None = None, to: str | None = None
    ) -> list[dict[str, Any]]:
        """^TPX の日次終値を [{symbol, date, close}] で返す（symbol は canonical ^TPX）。

        ^TPX 以外は IndexAdapterError を投げて次ソースへフォールバックさせる（J-Quants は海外/日経
        指数を持たないため）。JQuantsError（Free の 403 等）はそのまま伝播し、ファサードの except が
        握って次ソースへ回す（IndexAdapter.fetch_index_quotes）。
        """
        if symbol != "^TPX":
            raise IndexAdapterError(f"JQuantsIndexSource は ^TPX 専用です: {symbol}")
        fetch = self._fetch_topix
        if fetch is None:
            from app.adapters.jquants import JQuantsAdapter  # 遅延 import（API キーを要するため）

            fetch = JQuantsAdapter().fetch_topix
        rows = fetch(from_, to)
        return [{"symbol": "^TPX", "date": r["date"], "close": r["close"]} for r in rows]


# ソース名 → クラスのレジストリ（settings.index_sources の名前を解決）。
# 実ソース（yahoo/jquants 等）を足すときはここに登録する。
_REGISTRY: dict[str, type[IndexSource]] = {
    "yahoo": YahooIndexSource,
    "stooq": StooqIndexSource,
    "jquants": JQuantsIndexSource,
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
