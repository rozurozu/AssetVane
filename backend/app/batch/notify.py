"""Discord 通知の糊層（spec §3/§4・ADR-007/018）。

送信実体は adapters/discord.py（DiscordAdapter）へ移設・昇格した（Phase 6）。本モジュールは:
- error():     夜間バッチ/AI 失敗時のエラー通知（Phase 1/3 互換・冪等なし＝毎回送る最善努力）。
- send_once(): notify_key による冪等送信（Phase 6・二重送信防止）。
- send_test_notification(): Discord 疎通テスト（冪等回避＝毎回飛ぶ・ADR-011 の脳）。
を提供する薄い糊。DISCORD_WEBHOOK_URL 未設定なら no-op（アダプタが握る・ADR-018）。
通知は LINE Notify ではなく Discord Webhook（ADR-007）。
"""

from __future__ import annotations

import logging
import socket
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.adapters.discord import DiscordAdapter
from app.db import repo
from app.db.engine import get_engine

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TestNotifyResult:
    """Discord 疎通テストの結果（CLI/API/Web UI 共通の戻り値）。

    enabled: Webhook URL が設定されているか（False なら送信は no-op）。
    sent:    実際に 2xx で届いたか（enabled=False のときは常に False）。
    """

    enabled: bool
    sent: bool


def error(title: str, detail: str) -> None:
    """夜間バッチ/AI 失敗時に Discord へエラー通知する（spec §3.9・ADR-018）。

    webhook 未設定なら no-op（アダプタがログのみ）。送信失敗も握りつぶす（通知の失敗でバッチを
    落とさない）。冪等管理はしない（失敗通知は重複しても害が小さく、毎回気づきたいため）。
    """
    content = f"**[AssetVane バッチ失敗] {title}**\n{detail}"
    DiscordAdapter().send(content)


def send_once(
    notify_key: str,
    content: str,
    *,
    embeds: list[dict[str, Any]] | None = None,
    channel: str = "discord",
) -> bool:
    """notify_key で冪等に 1 通送る（spec §3・二重送信防止＝ADR-002/018）。

    1) 既送（notification_exists）なら送らず False。
    2) DiscordAdapter.send を呼ぶ。
    3) 送信成功（True）なら record_notification で記録する。
    送信失敗（False）は記録しない（翌実行で再試行＝at-least-once 受容・spec §3 注）。
    送信成功 → 記録の間で落ちると稀に再送するが、digest は同日同キーなので翌実行で重複しない。
    """
    with get_engine().connect() as conn:
        if repo.notification_exists(conn, notify_key, channel):
            logger.info("通知は既送のためスキップ: %s", notify_key)
            return False

    sent = DiscordAdapter().send(content, embeds=embeds)
    if sent:
        repo.record_notification(notify_key, channel, datetime.now(UTC).isoformat())
    return sent


def send_test_notification() -> TestNotifyResult:
    """Discord 疎通テストを 1 通送る（CLI/API/Web UI 共通の脳・ADR-011）。

    冪等（send_once）は通さず DiscordAdapter().send() を直接呼ぶ＝テストは何度叩いても飛ぶ。
    どの環境（dev/Pi）から来たか分かるよう host 名と送信時刻を載せる。Webhook 未設定なら
    enabled=False・sent=False を返す（アダプタが no-op・ADR-018）。送信失敗も例外を投げず
    enabled=True・sent=False（呼び出し側が「未設定」と「送信失敗」を区別できる）。
    """
    adapter = DiscordAdapter()
    if not adapter.enabled:
        logger.warning("Discord 未設定のため疎通テストをスキップ")
        return TestNotifyResult(enabled=False, sent=False)

    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    content = f"🔔 AssetVane Discord 疎通テスト — {socket.gethostname()} {stamp}"
    sent = adapter.send(content)
    return TestNotifyResult(enabled=True, sent=sent)
