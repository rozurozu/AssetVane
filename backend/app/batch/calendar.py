"""営業日候補の生成（spec §3.4・方式 X・裁定 L-3）。

start〜end を 'YYYY-MM-DD' で yield し、土日は曜日判定で除外する純ロジック。
祝日・臨時休場は営業日テーブルを持たず、`fetch_daily_quotes_by_date(d)` の**空配列で吸収**
する（カレンダー API に依存しない＝堅牢）。営業日テーブルを持たないのは B-3／裁定 L-3。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta


def _parse(d: str) -> date:
    """'YYYY-MM-DD' を date に変換する。"""
    return date.fromisoformat(d)


def candidate_days(start: str, end: str) -> Iterator[str]:
    """start〜end（両端含む）を 'YYYY-MM-DD' で yield。土日は曜日判定で除外（spec §3.4）。

    祝日はここで除外せず、バッチ側が空レスで吸収する。start > end なら何も yield しない。
    """
    cur = _parse(start)
    last = _parse(end)
    while cur <= last:
        # weekday(): 月=0 … 土=5・日=6。土日を除外。
        if cur.weekday() < 5:
            yield cur.isoformat()
        cur += timedelta(days=1)
