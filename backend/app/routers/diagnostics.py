"""診断系の REST ルータ（ADR-007/011/018）。

POST /diagnostics/discord-test。cron/CLI と同じ `send_test_notification()` を別口で叩く
（ADR-011「1つの脳・複数の起動口」）。Discord への POST は ~10s で完結するため、batch のような
非同期受付ではなく**同期**で送信結果（enabled/sent）を返し、Web UI が即座に成否を表示できる。
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.batch.notify import send_test_notification

router = APIRouter(tags=["diagnostics"])


class DiscordTestResponse(BaseModel):
    enabled: bool  # Webhook URL が設定されているか（false なら未設定で送らない）
    sent: bool  # 実際に 2xx で届いたか（enabled=false のときは常に false）


@router.post("/diagnostics/discord-test", response_model=DiscordTestResponse)
def discord_test() -> DiscordTestResponse:
    """Discord に疎通テストを 1 通送り、結果を返す（冪等回避＝毎回飛ぶ）。

    未設定（enabled=false）も送信失敗（sent=false）も例外にせず 200 で結果フラグとして返す
    （呼び出し側＝Web UI が両者を区別して表示するため）。送信実体・判定は notify.py が持つ。
    """
    result = send_test_notification()
    return DiscordTestResponse(enabled=result.enabled, sent=result.sent)
