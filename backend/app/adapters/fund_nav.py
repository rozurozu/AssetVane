"""投資信託 NAV（基準価額）アダプタ（FundNavAdapter）。

ADR-054: 投資信託の保有管理。NAV（基準価額）は外部 CSV から取得し fund_navs に焼く。
ADR-010: データソースはアダプタ越し。外部 CSV のフィールド名・文字コード・date 表記といった
「外の世界の都合」をこのファイルに閉じ込め、内側には内部列名（isin/date/nav）の安定した行だけを渡す。
ADR-018: 黙って 0 行にしない。HTML/JSON 混入（bot よけ・パラメータ不足）を検知し独自例外で投げる
（呼び出し側の batch が JobResult(ok=False) → Discord 通知へ翻訳できるように）。

取得元: 投信総合検索ライブラリー（ウエルスアドバイザー運営）の CSV。
  ダウンロード URL: `{base}/FdsWeb/FDST030000/csv-file-download?isinCd=<ISIN>&associFundCd=<協会>`
  - **associFundCd（協会コード）は必須**。欠落すると本文 `{"statusCode":null}`（データ無し）
    が返る（2026-06 実機確認）。funds.assoc_code を渡す。
  - 文字コードは **Shift_JIS（cp932）**。content-type ヘッダは `charset=utf-8` を名乗るが実体
    は SJIS（ヘッダを信用せず cp932 で decode する・2026-06 実機確認）。
  - CSV ヘッダ: `年月日,基準価額(円),純資産総額（百万円）,分配金,決算期`（年月日・基準価額のみ）。
  - 年月日は `YYYY年MM月DD日` 表記。基準価額は 10,000 口あたりの円（整数文字列・例 '38069'）。
  - サーバ側の日付範囲フィルタは無く、常に**設定来の全履歴**を返す。差分は from_/to で
    クライアント側に絞る（再取得は repo の UPSERT が冪等に吸収・ADR-002）。

外部キー名（年月日/基準価額(円)）→ 内部列名（date/nav）の対応はこのファイルに閉じ込める（ADR-010）。
"""

from __future__ import annotations

import csv
import io
import logging
import re
from typing import Any

import httpx

from app.adapters._http import DEFAULT_MAX_RETRIES, Throttle, get_with_retry
from app.config import settings

logger = logging.getLogger(__name__)

# CSV の外部列名（Shift_JIS の実ヘッダ・2026-06 実機確認）。内部列名 date/nav へ正規化する。
_COL_DATE = "年月日"
_COL_NAV = "基準価額(円)"

# 年月日 'YYYY年MM月DD日' を分解する正規表現（前後の空白も許容）。
_DATE_RE = re.compile(r"^\s*(\d{4})年(\d{1,2})月(\d{1,2})日\s*$")

# 既定のスロットル間隔（秒）。Free 系の外部サイトに優しく（設定が無ければこの既定）。
_DEFAULT_MIN_INTERVAL_SECONDS = 1.0


class FundNavFetchError(RuntimeError):
    """投信 NAV 取得のエラー（HTTP 失敗・パラメータ不足・HTML/JSON 混入・パース不能等）。

    用途別の独自例外（ADR-010・backend-foundations）。呼び出し側（batch）が JobResult/通知へ翻訳。
    """


def _norm_date(value: str) -> str | None:
    """'YYYY年MM月DD日' を 'YYYY-MM-DD' に正規化する。形式不一致は None（呼び出し側でスキップ）。"""
    m = _DATE_RE.match(value)
    if not m:
        return None
    year, month, day = m.groups()
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"


class FundNavAdapter:
    """投信総合検索ライブラリーの CSV から NAV（基準価額）時系列を取る（ADR-010/054）。

    `fetch_nav_history(isin, assoc_code, from_, to)` で内部列名（isin/date/nav）の行を返す。
    HTTP・文字コード・date 表記・HTML/JSON 混入検知・スロットル・リトライをこのクラスに閉じ込める。
    `client` 引数でテスト用のスタブ httpx.Client を注入できる（実 API・ネットに出さない
    ＝testing-strategy）。
    """

    def __init__(
        self,
        base_url: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url or settings.fund_nav_base_url
        self._client = client  # テスト注入用（None なら都度作成）
        # スロットル間隔は設定から読む（無ければ既定）。Free 系サイトに優しく（ADR-010）。
        self._throttle = Throttle(
            settings.fund_nav_min_interval_seconds or _DEFAULT_MIN_INTERVAL_SECONDS
        )
        self._timeout = settings.fund_nav_http_timeout_seconds

    def _get_csv_bytes(self, isin: str, assoc_code: str) -> bytes:
        """CSV を生バイトで取得する（cp932 decode は呼び出し側）。HTTP 失敗は FundNavFetchError。

        associFundCd（協会コード）は必須。欠落時はサイトが JSON（statusCode:null）を返すため、
        呼び出し側で空 assoc_code を弾く（ここには来ない前提だが、来ても JSON 混入検知で拾う）。
        429 のみリトライしネットワーク例外は呼び出し側へ透過する
        （従来挙動＝retry_network_errors=False・既存テスト互換）。
        """
        params = {"isinCd": isin, "associFundCd": assoc_code}
        path = "/FdsWeb/FDST030000/csv-file-download"

        def do_request(c: httpx.Client) -> bytes:
            resp = get_with_retry(
                c,
                path,
                params=params,
                throttle=self._throttle,
                retry_network_errors=False,
                on_http_error=lambda r: FundNavFetchError(
                    f"投信 NAV CSV GET isin={isin} が {r.status_code}:"
                    f" {r.text[:200]}（associFundCd={assoc_code}）"
                ),
                on_exhausted=lambda _e: FundNavFetchError(
                    f"投信 NAV CSV GET isin={isin} がレート制限で"
                    f" {DEFAULT_MAX_RETRIES} 回失敗しました。"
                ),
            )
            return resp.content

        if self._client is not None:
            return do_request(self._client)
        with httpx.Client(base_url=self._base_url, timeout=self._timeout) as c:
            return do_request(c)

    @staticmethod
    def _decode(isin: str, raw: bytes) -> str:
        """生バイトを Shift_JIS（cp932）で decode する（content-type ヘッダは信用しない・ADR-010）。

        サイトは content-type に charset=utf-8 を名乗るが実体は SJIS（2026-06 実機確認）。
        cp932 で decode 不能なら HTML/別エンコードの混入を疑い FundNavFetchError を投げる。
        """
        try:
            return raw.decode("cp932")
        except UnicodeDecodeError as exc:
            snippet = raw[:200]
            raise FundNavFetchError(
                f"投信 NAV CSV isin={isin} を Shift_JIS で decode できませんでした"
                f"（HTML/別文字コードの混入の疑い）: {snippet!r}"
            ) from exc

    @staticmethod
    def _ensure_csv(isin: str, text: str) -> None:
        """応答が想定 CSV か検証する（ADR-018: 黙って 0 行にしない）。

        associFundCd 不足・bot よけ・障害時はサイトが JSON（`{"statusCode":null}`）や HTML を
        HTTP 200 で返す。それを CSV としてパースすると無言で 0 行になり「静かな失敗」になる。
        想定ヘッダ（年月日 で始まる）でなければ FundNavFetchError を投げ、batch を ok=False に倒す。
        """
        first_line = text.lstrip().splitlines()[0] if text.strip() else ""
        if not first_line.startswith(_COL_DATE):
            snippet = " ".join(text.split())[:200]
            raise FundNavFetchError(
                f"投信 NAV isin={isin} が想定 CSV を返しませんでした"
                f"（協会コード不足/bot よけ/障害の疑い）: {snippet or '<空応答>'}"
            )

    def fetch_nav_history(
        self,
        isin: str,
        assoc_code: str | None = None,
        from_: str | None = None,
        to: str | None = None,
    ) -> list[dict[str, Any]]:
        """ISIN の NAV 時系列を取得し内部列名に正規化して返す（ADR-010/054）。

        戻り値: [{"isin": isin, "date": "YYYY-MM-DD", "nav": float}, ...]（date 昇順は元 CSV 順）。
        nav は CSV の基準価額(円)（10,000 口あたりの円）をそのまま float 化。
        CSV はサーバ側の日付フィルタを持たず常に設定来の全履歴を返すため、from_/to は
        クライアント側で絞る（差分取得の重複は repo の UPSERT が冪等に吸収・ADR-002）。
        assoc_code（協会コード）は CSV ダウンロードに**必須**。欠落時は FundNavFetchError を投げ、
        呼び出し側が「取得手段の無い銘柄」として握れるようにする（黙って 0 行にしない・ADR-018）。
        """
        if not assoc_code:
            raise FundNavFetchError(
                f"投信 NAV isin={isin} は協会コード（assoc_code）が未設定のため取得できません"
                "（CSV ダウンロードに associFundCd が必須・funds.assoc_code を登録してください）。"
            )

        raw = self._get_csv_bytes(isin, assoc_code)
        text = self._decode(isin, raw)
        self._ensure_csv(isin, text)

        rows: list[dict[str, Any]] = []
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            date_raw = (row.get(_COL_DATE) or "").strip()
            nav_raw = (row.get(_COL_NAV) or "").strip()
            if not date_raw or not nav_raw:
                continue
            date = _norm_date(date_raw)
            if date is None:
                continue
            # from_/to はクライアント側で絞る（CSV は全履歴を返すため・ADR-002）。
            if from_ is not None and date < from_:
                continue
            if to is not None and date > to:
                continue
            try:
                nav = float(nav_raw)
            except ValueError:
                continue
            rows.append({"isin": isin, "date": date, "nav": nav})
        return rows
