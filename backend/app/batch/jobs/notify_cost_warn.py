"""夜間バッチ: LLM 月額コスト warn 通知ジョブ（ADR-028・spec §7.1）。

NIGHTLY_JOBS の通知系（notify_digest の直前）で呼ばれ、当月の LLM コスト累計が上限を
超えていれば **その月最初の夜に 1 通だけ** Discord へ警告する（ADR-028 の warn＝
「止めずに気づかせる」）。

設計の芯:
- **発火源はコスト超過のみ**。`mode == "warn"` かつ `sum_llm_cost_month(当月) >= 上限` の
  ときだけ送る。block は別経路（呼び出し時に CostGuardError → チャットエラー／夜 ok=False）、
  off は監視なしなので、いずれもここでは送らない。
- **送信トリガを batch に置く**理由（ADR-028）: warn 検知点は `advisor/llm.py:_check_cost_guard`
  だが、そこから通知を送ると `advisor → batch` の逆流依存になる。画面バナー（/health 集計）が
  即時性を担保するので、Discord は夜間バッチに寄せてレイヤを綺麗に保つ。
- **月境界は UTC**（`advisor/llm.py:_current_month` と同一算式。逆流回避のため import せず自前）。
- **冪等**（ADR-002/018）。notify_key='llm_cost_warn:<UTC 年月>' で月内 1 通。POST /batch/run の
  手動再実行や月内の複数夜で 2 通目は送らない。翌月キーが変わって再び 1 通だけ送れる。
- 例外は握って JobResult(ok=False)（runner が error 通知）。送信失敗で本処理は巻き込まない。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.batch import notify
from app.batch.runner import JobResult
from app.config import settings
from app.db import repo
from app.db.engine import get_engine

logger = logging.getLogger(__name__)


def build_cost_warn_content(total: float, limit: float, month: str) -> str:
    """warn 通知の本文を組み立てる（ADR-014: 金額は Python の事実から整形・AI に計算させない）。

    total/limit は当月累計と上限（USD）。month は 'YYYY-MM'（UTC）。
    """
    return (
        f"**⚠️ AssetVane LLM 月額コスト警告（{month}）**\n"
        "\n"
        "クラウド LLM の当月累計コストが上限に達しました。\n"
        f"・当月累計: ${total:.2f}\n"
        f"・上限: ${limit:.2f}（mode=warn・呼び出しは継続）\n"
        "\n"
        "warn 設定のため上限超過後も AI 応答は止まりません。\n"
        "超過が続く場合は LLM_COST_GUARD_MODE を block に切り替えるか、利用を控えてください。"
    )


def run() -> JobResult:
    """当月 LLM コストが上限超過なら警告を 1 通冪等送信する（ADR-028・spec §7.1）。

    例外は握って JobResult(ok=False)（runner が error 通知）。送信失敗で本処理は巻き込まない。
    """
    month = datetime.now(UTC).strftime("%Y-%m")
    try:
        mode = settings.llm_cost_guard_mode
        limit = settings.llm_cost_limit_usd
        with get_engine().connect() as conn:
            total = repo.sum_llm_cost_month(conn, month)

        # warn 限定＋未超過は no-op（off/block は対象外＝設計上の固定）。
        if mode != "warn" or total < limit:
            return JobResult(
                name="notify_cost_warn", ok=True, rows=0, detail="未超過/対象外（送信なし）"
            )

        content = build_cost_warn_content(total, limit, month)
        sent = notify.send_once(f"llm_cost_warn:{month}", content)
        detail = "コスト警告 送信" if sent else "コスト警告 送信せず（既送 or Webhook 未設定/失敗）"
        return JobResult(name="notify_cost_warn", ok=True, rows=1 if sent else 0, detail=detail)
    except Exception as exc:  # noqa: BLE001 — ジョブ境界で握り runner に返す
        logger.exception("notify_cost_warn が失敗")
        return JobResult(name="notify_cost_warn", ok=False, rows=0, detail=f"失敗: {exc}")
