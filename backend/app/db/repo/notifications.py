"""Signal Beacon 通知の冪等台帳（Phase 6・ADR-007/018）。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Connection, select

from app.db.repo._common import _upsert
from app.db.schema import (
    advisor_journal,
    notifications,
)

# ===== Phase 6: Signal Beacon（phase6-spec.md §2/§3・ADR-007/018・0010_notifications） =====


def notification_exists(conn: Connection, notify_key: str, channel: str) -> bool:
    """notify_key が既に送信済みか返す（二重送信防止の存在確認・spec §3）。

    send_once が送信前にこれを見て True なら送らない（冪等＝ADR-002/018）。
    """
    stmt = (
        select(notifications.c.notify_key)
        .where(notifications.c.notify_key == notify_key)
        .where(notifications.c.channel == channel)
        .limit(1)
    )
    return conn.execute(stmt).first() is not None


def record_notification(notify_key: str, channel: str, sent_at: str) -> None:
    """送信済みを記録する（spec §3・冪等 UPSERT）。

    (notify_key, channel) 衝突時は sent_at を更新するだけ（再記録は実質 no-op）。単発の書き込みは
    repo が自前で begin する（W1・add_watchlist と同じ流儀）。送信成功 → 記録の間で落ちると稀に
    再送するが、digest は同日同キーなので翌実行で重複しない（at-least-once 受容・spec §3 注）。
    """
    _upsert(
        notifications,
        [{"notify_key": notify_key, "channel": channel, "sent_at": sent_at}],
        index_elements=["notify_key", "channel"],
    )


def get_journal_for_date(conn: Connection, date: str) -> dict[str, Any] | None:
    """指定日の夜の分析AI journal（最新 1 行）を返す（当日提案プッシュの素・spec §3）。

    proposal / proposed_policy_change を digest に要約引用する（Phase 3 生成済み文をそのまま使う
    ＝AI に再計算させない・ADR-014/016）。source='nightly' を優先し、同日複数あれば id 降順で
    最新を採る。無ければ None。
    """
    stmt = (
        select(advisor_journal)
        .where(advisor_journal.c.date == date)
        .where(advisor_journal.c.source == "nightly")
        .order_by(advisor_journal.c.id.desc())
        .limit(1)
    )
    row = conn.execute(stmt).mappings().first()
    return dict(row) if row else None
