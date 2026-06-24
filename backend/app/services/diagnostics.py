"""外部依存の疎通テスト（脳・ADR-008/011/018/036）。

discord-test（batch/notify.send_test_notification）と同じ思想で、外部依存に 1 発だけ投げて
**生死を確認する脳**。CLI（make）/REST（/diagnostics/...）/WebUI の複数の起動口から同じ関数を
叩く（ADR-011）。接続値（api_key/plan）は env から DB（jquants_config）へ移管したため（ADR-061）、
解決のため `conn` を受け取り DB を読む（DB に触れるのは FastAPI＝ADR-005）。

J-Quants は `fetch_master(["7203"])` で「`x-api-key` 認証が通る＋1 銘柄返る」を確認する認証ピング
（V2・ADR-008）。鮮度（Free の12週遅延でどこまで取れるか）は前線まで日付を遡る探索になり「ピング」
には重すぎるため、ここでは見ない（grill-me 合意）。例外は握って結果に畳む（呼び出し側が未設定／
失敗を区別して表示する・ADR-018）。LLM 疎通（check_llm）も将来ここに同型で足す。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import Connection

from app.adapters.jquants import JQuantsError
from app.services.jquants_config import build_jquants_adapter, resolve_jquants_config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class JquantsCheckResult:
    """J-Quants 疎通テストの結果（CLI/REST/WebUI 共通の戻り値）。

    configured: API キーが設定されているか（False なら呼ばずに未設定で返す）。
    ok:         認証が通り 1 銘柄取れたか（configured=False のときは常に False）。
    detail:     人間向けメッセージ（成功＝会社名／失敗＝エラー要旨）。
    """

    configured: bool
    ok: bool
    detail: str


def check_jquants(conn: Connection) -> JquantsCheckResult:
    """J-Quants V2 に認証ピングを 1 発投げて生死を返す（接続値は DB 解決・ADR-008/011/061）。

    キー未設定（jquants_config 未登録）なら configured=False で即返す。`fetch_master(["7203"])` を
    叩き、JQuantsError（キー不正・HTTP 失敗等）も予期せぬ例外も握って ok=False＋要旨に畳む。成功時は
    会社名と件数を detail に載せる（Pi の WebUI から初回デプロイ前に疎通を確かめられるように）。
    """
    if resolve_jquants_config(conn) is None:
        logger.warning("J-Quants 未設定のため疎通テストをスキップ")
        return JquantsCheckResult(
            configured=False, ok=False, detail="J-Quants API キーが未設定です"
        )

    try:
        rows = build_jquants_adapter(conn).fetch_master(["7203"])
    except JQuantsError as exc:
        return JquantsCheckResult(configured=True, ok=False, detail=str(exc)[:200])
    except Exception as exc:  # noqa: BLE001 — 疎通確認は例外を握って結果に畳む（ADR-018）
        logger.exception("J-Quants 疎通テストで予期せぬ失敗")
        return JquantsCheckResult(
            configured=True, ok=False, detail=f"{type(exc).__name__}: {exc}"[:200]
        )

    if not rows:
        return JquantsCheckResult(
            configured=True, ok=False, detail="認証は通ったが 7203 が 0 件で返りました"
        )
    name = rows[0].get("company_name") or "?"
    return JquantsCheckResult(
        configured=True, ok=True, detail=f"認証OK・7203={name}（{len(rows)} 件取得）"
    )
