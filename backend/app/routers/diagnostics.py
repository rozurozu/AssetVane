"""診断系の REST ルータ（ADR-007/008/011/018/036）。

cron/CLI と同じ脳を別口で叩く（ADR-011「1つの脳・複数の起動口」）。いずれも外部依存への 1 発で
~10s 以内に完結するため、batch のような非同期受付ではなく**同期**で結果フラグを返し、Web UI が
即座に成否を表示できる。
- POST /diagnostics/discord-test … Discord 疎通（send_test_notification・enabled/sent）。
- POST /diagnostics/jquants-test … J-Quants 認証ピング（check_jquants・接続値は DB 解決・ADR-061）。
- POST /diagnostics/edinetdb-test … EDINET DB 認証ピング（check_edinetdb・接続値は DB 解決・
ADR-064）。
- POST /diagnostics/edinet-test … 公式 EDINET 認証ピング（check_edinet・DB 解決・ADR-087）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import Connection

from app.batch.notify import send_test_notification
from app.db.engine import get_conn
from app.services.diagnostics import check_edinet, check_edinetdb, check_jquants

router = APIRouter(tags=["diagnostics"])


class DiscordTestResponse(BaseModel):
    enabled: bool  # Webhook URL が設定されているか（false なら未設定で送らない）
    sent: bool  # 実際に 2xx で届いたか（enabled=false のときは常に false）


class JquantsTestResponse(BaseModel):
    configured: bool  # API キーが設定されているか（false なら呼ばずに未設定で返す）
    ok: bool  # 認証が通り 1 銘柄取れたか（configured=false のときは常に false）
    detail: str  # 人間向けメッセージ（成功＝会社名／失敗＝エラー要旨）


@router.post("/diagnostics/discord-test", response_model=DiscordTestResponse)
def discord_test() -> DiscordTestResponse:
    """Discord に疎通テストを 1 通送り、結果を返す（冪等回避＝毎回飛ぶ）。

    未設定（enabled=false）も送信失敗（sent=false）も例外にせず 200 で結果フラグとして返す
    （呼び出し側＝Web UI が両者を区別して表示するため）。送信実体・判定は notify.py が持つ。
    """
    result = send_test_notification()
    return DiscordTestResponse(enabled=result.enabled, sent=result.sent)


@router.post("/diagnostics/jquants-test", response_model=JquantsTestResponse)
def jquants_test(conn: Connection = Depends(get_conn)) -> JquantsTestResponse:
    """J-Quants V2 に認証ピングを 1 発投げ、結果を返す（接続値は DB 解決・ADR-008/011/036/061）。

    未設定（configured=false）も疎通失敗（ok=false）も例外にせず 200 で結果フラグとして返す
    （Web UI が両者を区別して表示するため）。実体・判定は services/diagnostics.py が持つ。
    """
    result = check_jquants(conn)
    return JquantsTestResponse(configured=result.configured, ok=result.ok, detail=result.detail)


class EdinetDbTestResponse(BaseModel):
    configured: bool  # API キーが設定されているか（false なら呼ばずに未設定で返す）
    ok: bool  # 認証が通り会社一覧が取れたか（configured=false のときは常に false）
    detail: str  # 人間向けメッセージ（成功＝収載社数＋月残予算／失敗＝エラー要旨）


@router.post("/diagnostics/edinetdb-test", response_model=EdinetDbTestResponse)
def edinetdb_test(conn: Connection = Depends(get_conn)) -> EdinetDbTestResponse:
    """edinetdb.jp に認証ピングを 1 発投げ、結果を返す（接続値は DB 解決・ADR-064）。

    未設定（configured=false）も疎通失敗（ok=false）も例外にせず 200 で結果フラグとして返す
    （Web UI が両者を区別して表示するため）。実体・判定は services/diagnostics.py が持つ。
    """
    result = check_edinetdb(conn)
    return EdinetDbTestResponse(configured=result.configured, ok=result.ok, detail=result.detail)


class EdinetTestResponse(BaseModel):
    configured: bool  # API キーが設定されているか（false なら呼ばずに未設定で返す）
    ok: bool  # Subscription-Key 認証が通り書類一覧が取れたか（configured=false のときは常に false）
    detail: str  # 人間向けメッセージ（成功＝当日書類件数／失敗＝エラー要旨・貼り間違い検知）


@router.post("/diagnostics/edinet-test", response_model=EdinetTestResponse)
def edinet_test(conn: Connection = Depends(get_conn)) -> EdinetTestResponse:
    """公式 EDINET に認証ピングを 1 発投げ、結果を返す（接続値は DB 解決・ADR-056/087）。

    未設定（configured=false）も疎通失敗（ok=false）も例外にせず 200 で結果フラグとして返す
    （Web UI が両者を区別して表示するため）。実体・判定は services/diagnostics.py が持つ。
    """
    result = check_edinet(conn)
    return EdinetTestResponse(configured=result.configured, ok=result.ok, detail=result.detail)
