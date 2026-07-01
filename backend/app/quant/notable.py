"""注目候補の合流判定に使う純関数（ADR-067 材料①「値動き」）。

設計の真実: docs/decisions.md ADR-067・docs/phase-specs/phase6-spec.md。

夜 digest の「注目シグナル」を合流(confluence)ゲートで作り直す（ADR-067）。その材料①「値動き」は
GC/RSI 反転（momentum の payload・quant/momentum.py）に加えて「当日の大幅変動」を含む。GC/RSI 反転は
特定パターンでギャップアップ/急落を取りこぼすため、素の前日比を材料に足す。

計算境界（ADR-014/016）: ここは DB を知らない純関数（終値系列 → 前日比）。閾値判定（何 % を大幅と
みなすか＝BIG_MOVE_PCT）は services/notable.py が持つ（手法パラメータの置き場＝ADR-027）。
"""

from __future__ import annotations

from collections.abc import Sequence


def daily_move_pct(closes: Sequence[float | None]) -> float | None:
    """終値系列（日付昇順）の当日前日比を返す（ADR-067 材料①）。

    直近 2 本（前日・当日）の終値から `last / prev - 1` を符号付きで返す（急騰=正・急落=負）。
    呼び出し側が abs で「大幅か」を判定する。以下は None（捏造しない＝ADR-014）:
    - 2 本未満（新規上場等でヒストリ不足）
    - 前日終値が 0 以下（比率が定義できない）
    - 前日 or 当日が欠損（None）

    分割・併合の影響を避けるため adj_close 系列を渡すこと（services が adj_close を渡す）。
    """
    if len(closes) < 2:
        return None
    prev, last = closes[-2], closes[-1]
    if prev is None or last is None:
        return None
    prev_f = float(prev)
    if prev_f <= 0.0:
        return None
    return float(last) / prev_f - 1.0
