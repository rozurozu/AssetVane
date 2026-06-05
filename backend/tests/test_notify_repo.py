"""Phase 6 repo 関数の検証（notifications・list_signals_for_alert・get_journal_for_date）。

冪等送信の存在確認/記録、⑧アラートの素（signals JOIN stocks）、当日提案の素（advisor_journal）を
一時 SQLite で確かめる（spec §2/§3）。
"""

from __future__ import annotations

import json

from app.db import repo
from app.db.engine import get_engine

STOCK = {
    "code": "72030",
    "company_name": "トヨタ自動車",
    "sector33_code": "3700",
    "sector17_code": "6",
    "market_code": "0111",
    "is_etf": 0,
    "updated_at": "2026-06-02T00:00:00+00:00",
}


def test_notification_exists_and_record(temp_db) -> None:
    """record_notification 前は不在・後は存在。再記録は重複行を作らない（冪等）。"""
    key = "digest:2026-06-01"
    with get_engine().connect() as conn:
        assert repo.notification_exists(conn, key, "discord") is False

    repo.record_notification(key, "discord", "2026-06-01T17:00:00+00:00")
    with get_engine().connect() as conn:
        assert repo.notification_exists(conn, key, "discord") is True
        # channel 違いは別キー扱い。
        assert repo.notification_exists(conn, key, "slack") is False

    # 再記録（sent_at を変える）→ 行は増えず上書きされるだけ（冪等 UPSERT）。
    repo.record_notification(key, "discord", "2026-06-01T18:00:00+00:00")
    with get_engine().connect() as conn:
        rows = conn.exec_driver_sql(
            "SELECT sent_at FROM notifications WHERE notify_key = ? AND channel = ?",
            (key, "discord"),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "2026-06-01T18:00:00+00:00"


def test_list_signals_for_alert_joins_company_name(temp_db) -> None:
    """指定日の signals を company_name 付き・score 降順で返す。"""
    repo.upsert_stocks([STOCK])
    repo.upsert_signals(
        [
            {
                "date": "2026-06-01",
                "code": "72030",
                "signal_type": "momentum",
                "score": 0.4,
                "payload": json.dumps({"label": "弱め"}, ensure_ascii=False),
            },
            {
                "date": "2026-06-01",
                "code": "72030",
                "signal_type": "volume_spike",
                "score": 0.8,
                "payload": json.dumps({"ratio": 3.5, "label": "出来高急増"}, ensure_ascii=False),
            },
            {
                "date": "2026-05-31",  # 別日（対象外）
                "code": "72030",
                "signal_type": "momentum",
                "score": 0.9,
                "payload": "{}",
            },
        ]
    )

    with get_engine().connect() as conn:
        rows = repo.list_signals_for_alert(conn, "2026-06-01")

    assert [r["signal_type"] for r in rows] == ["volume_spike", "momentum"]  # score 降順
    assert rows[0]["company_name"] == "トヨタ自動車"
    assert json.loads(rows[0]["payload"])["ratio"] == 3.5  # payload は生文字列のまま


def test_get_journal_for_date_picks_latest_nightly(temp_db) -> None:
    """当日の nightly journal の最新 1 行を返す。chat 源は拾わない。無い日は None。"""
    with get_engine().begin() as conn:
        repo.insert_journal(
            conn,
            date="2026-06-01",
            source="nightly",
            observations="様子見",
            proposal="現金比率を上げる検討",
            proposed_policy_change=json.dumps({"field": "target_cash_ratio", "to": 0.2}),
        )
        repo.insert_journal(
            conn,
            date="2026-06-01",
            source="chat",  # 昼チャット昇格は対象外
            observations="雑談",
            proposal="無視されるべき",
        )

    with get_engine().connect() as conn:
        row = repo.get_journal_for_date(conn, "2026-06-01")
        none_row = repo.get_journal_for_date(conn, "2026-06-02")

    assert row is not None
    assert row["source"] == "nightly"
    assert row["proposal"] == "現金比率を上げる検討"
    assert json.loads(row["proposed_policy_change"])["field"] == "target_cash_ratio"
    assert none_row is None
