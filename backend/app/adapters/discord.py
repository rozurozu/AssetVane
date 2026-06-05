"""Discord Webhook 送信アダプタ（DiscordAdapter）— Phase 6 Signal Beacon の送信実体。

設計の真実: docs/phase-specs/phase6-spec.md §4／ADR-007・ADR-010・ADR-018。

ADR-007: 通知は LINE Notify ではなく Discord Webhook（無料・登録不要・軽量）。
ADR-010: 外部送信はアダプタ越し。Webhook URL は config（settings.discord_webhook_url）から読み、
        ハードコードしない。
ADR-018: 送信失敗（4xx/5xx・接続エラー・タイムアウト）でも例外を投げず False を返す。通知の失敗で
        夜間バッチの本処理（取得・signals・日記）を巻き込まない。

Phase 1 の batch/notify.py（エラー通知最小版）の送信実体をここへ移設・昇格した。notify.py は
error()/send_once() の薄い糊として残り、送信は本アダプタを呼ぶ（spec §4）。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Discord メッセージ content の上限（2000 字）に収めるための上限（余裕を持たせる）。
_MAX_CONTENT = 1900
_HTTP_TIMEOUT_SECONDS = 10.0


class DiscordAdapter:
    """Discord Webhook 送信（ADR-007/010/018）。

    Webhook URL は既定で settings.discord_webhook_url（.env 固定・秘密情報は backend のみ）。
    未設定なら send は no-op で False を返す（Phase 0〜5 でも import で壊れない）。
    """

    def __init__(self, webhook_url: str | None = None) -> None:
        # 既定は settings から読む（ADR-010・ハードコード禁止）。空なら無効（送らない）。
        self._webhook_url = settings.discord_webhook_url if webhook_url is None else webhook_url

    @property
    def enabled(self) -> bool:
        """Webhook URL が設定されているか（未設定なら送信は no-op）。"""
        return bool(self._webhook_url)

    def send(self, content: str, *, embeds: list[dict[str, Any]] | None = None) -> bool:
        """content（＋任意 embeds）を Discord Webhook へ POST する。

        2xx で True。4xx/5xx・接続エラー・タイムアウトは例外を投げず False（ADR-018）。
        webhook_url 未設定なら送らず False（ログのみ）。content は 1900 字で截断する。
        """
        if not self._webhook_url:
            logger.warning("Discord 未設定のため送信スキップ: %s", content[:80])
            return False

        payload: dict[str, Any] = {"content": content[:_MAX_CONTENT]}
        if embeds:
            payload["embeds"] = embeds

        try:
            resp = httpx.post(self._webhook_url, json=payload, timeout=_HTTP_TIMEOUT_SECONDS)
        except httpx.HTTPError as exc:
            # 通信エラー（接続失敗・タイムアウト）。通知失敗で本処理を巻き込まない（ADR-018）。
            logger.warning("Discord 送信に失敗（通信エラー）: %s", exc)
            return False
        if resp.status_code >= 400:
            logger.warning("Discord 送信に失敗（%s）: %s", resp.status_code, resp.text[:200])
            return False
        return True
