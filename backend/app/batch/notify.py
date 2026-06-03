"""Discord エラー通知（spec §3.9・ADR-018・ADR-007）。

Phase 1 は**エラー時のみ**通知する（成功サマリ・シグナル通知は Phase 6・webhook.py へ昇格）。
`DISCORD_WEBHOOK_URL` 未設定なら no-op（ログのみ）。httpx POST に失敗しても握りつぶす
（通知の失敗でバッチを落とさない）。通知は LINE Notify ではなく Discord Webhook（ADR-007）。
"""

from __future__ import annotations

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Discord メッセージの content 上限（2000 字）に収めるための上限。
_MAX_CONTENT = 1900


def error(title: str, detail: str) -> None:
    """夜間バッチ失敗時に Discord へエラー通知する（spec §3.9）。

    webhook 未設定ならログのみで no-op。POST 失敗（タイムアウト・4xx/5xx）も握りつぶす。
    """
    content = f"**[AssetVane バッチ失敗] {title}**\n{detail}"[:_MAX_CONTENT]

    url = settings.discord_webhook_url
    if not url:
        # 未設定時はログだけ残す（家庭内 LAN・単一ユーザー前提＝ADR-001）。
        logger.error("batch error (Discord 未設定): %s / %s", title, detail)
        return

    try:
        httpx.post(url, json={"content": content}, timeout=10.0)
    except httpx.HTTPError as exc:
        # 通知の失敗でバッチ自体を落とさない（観測性は最善努力）。
        logger.warning("Discord 通知に失敗: %s", exc)
