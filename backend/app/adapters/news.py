"""ニュース取得アダプタ（NewsAdapter）— Phase 4 ドシエ用のソース取得（スタブ）。

設計の真実: docs/phase-specs/phase4-spec.md §3・§4・§7／ADR-010・ADR-020。

ADR-010: 外部データソースはアダプタ越しに使う（router/service/batch から直結しない）。
ADR-020: ドシエは「取得 → 要約 → 本文は捨てる」。ここで取得した記事は summary と url のみ
        ドシエ台帳（dossier_sources）へ残し、本文は保存しない。

取得手段は `mode` で切り替える（spec §3・§7）:
  - mode="chat":    昼チャット経路。MCP（playwright/fetch）でリッチに取得する想定。
  - mode="nightly": 夜間 cron 経路。無人でヘッドレス（MCP）が使えないことがあるため
                    MCP 非依存・軽め（既存 httpx で軽く取得する想定）。

このイテレーションでは **実取得は未実装のスタブ**。両 mode とも分岐の骨だけ用意し、空配列を
返す（開発用ダミーを返したい場合はここに差す）。実取得（昼 MCP の接続・夜 httpx の RSS 等）は
次イテレーションで本アダプタに差す（spec §7・ADR-020）。`investigate_stock` のテストが本関数を
差し替えられるよう、module-level の `fetch_news` を取得境界（モックポイント）とする。
"""

from __future__ import annotations

import logging
from typing import Literal

logger = logging.getLogger(__name__)


class NewsAdapterError(RuntimeError):
    """NewsAdapter の取得エラー（HTTP 失敗・パースエラー等）。

    実取得を差したあと、取得失敗時にこの独自例外で投げる。呼び出し側（investigate_stock）は
    Tool ループを落とさないよう握って扱う（spec §4）。現スタブでは送出しない。
    """


async def _fetch_chat(code: str, *, since: str | None) -> list[dict]:
    """昼チャット経路の取得（MCP リッチ・スタブ）。

    本実装では MCP（playwright/fetch）で記事を取得して要約する想定（spec §3・§7）。
    現状はスタブで空配列を返す。
    """
    logger.debug("fetch_news(chat) スタブ: code=%s since=%s（未実装・空配列を返す）", code, since)
    return []


async def _fetch_nightly(code: str, *, since: str | None) -> list[dict]:
    """夜間 cron 経路の取得（MCP 非依存・軽め・スタブ）。

    本実装では既存 httpx で軽く取得する想定（RSS 等・MCP 非依存＝ADR-020）。
    現状はスタブで空配列を返す。
    """
    logger.debug(
        "fetch_news(nightly) スタブ: code=%s since=%s（未実装・空配列を返す）", code, since
    )
    return []


async def fetch_news(
    code: str,
    *,
    since: str | None = None,
    mode: Literal["nightly", "chat"],
) -> list[dict]:
    """指定銘柄のニュース記事を取得して返す（spec §3・§4 の返却スキーマが正本・ADR-010/020）。

    Args:
        code: 銘柄コード。
        since: 取得下限日 'YYYY-MM-DD'（発行 1 週間以内に絞る・spec §3）。None なら無制限。
        mode: 取得手段の切替。"chat"=昼 MCP リッチ／"nightly"=夜 MCP 非依存・軽め。

    Returns:
        記事 dict の list。各 article は
        `{url, title, summary, published_at, source_type}`（spec §4 の返却スキーマ正本）。
        **本文（全文）は返さない**（要約のみ＝ADR-020）。現スタブは両 mode とも空配列。

    Note:
        実取得は次イテレーションで本アダプタに差す（昼 MCP・夜 httpx／spec §7・ADR-020）。
        テストは本関数（module-level）を差し替えてパイプラインを検証する（モックポイント）。
    """
    if mode == "chat":
        return await _fetch_chat(code, since=since)
    return await _fetch_nightly(code, since=since)
