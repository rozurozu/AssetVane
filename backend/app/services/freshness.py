"""データ鮮度（遅延）判定の共有純関数（ADR-071・backend-service-quant-pattern）。

設計の真実: docs/decisions.md ADR-071（is_delayed を「プラン仮定」から「as_of vs today の
鮮度実測」に一元化）。関連: ADR-008（プラン別の株価遅延）・ADR-061（右上バッジを plan 由来に）・
ADR-014/016（Python が事実を計算・quant は today を知らない純関数）。

なぜプランを読まないか: J-Quants Free プランは物理的に約 12 週遅れのデータしか配信しないため、
DB の最新 daily_quotes 行の日付（as_of）が約 84 日前になる。ゆえにプランを読まずとも
(today - as_of) >= 閾値 で Free 遅延は自動的に True になる。同じ判定で「有料プランだが夜間
バッチが止まって古い（stale）」も捕まえられる（プランベースの判定では見逃す）。US（yfinance）は
J-Quants のプラン概念が無いが、同じ鮮度判定で stale を捕まえられる（市場非依存＝JP/US で共用）。

従来の重複（advisor tools の `_IS_DELAYED=True` 固定・`_signals_is_delayed`、routers の signals・
portfolio・assets の各ローカル `_is_delayed`／True 固定）をこの 1 本に集約する。DB を知らない
純関数なので routers と advisor tool handlers の両方から import できる。
"""

from __future__ import annotations

import datetime

# 算出日（as_of）が today からこの日数以上前なら遅延扱い（暦日で素朴に判定・従来 signals と同値）。
# 連休直後は新鮮でも数日空くため、営業日でなく暦日の 7 日を境界にして誤検知を抑える。
_STALE_THRESHOLD_DAYS = 7


def is_delayed(
    as_of: str | None,
    today: datetime.date | None = None,
    threshold_days: int = _STALE_THRESHOLD_DAYS,
) -> bool:
    """データ時点 as_of が古ければ True を返す（鮮度実測・ADR-071）。

    - `as_of` は "YYYY-MM-DD"。`None`（データ無・未取得）や parse 不能な文字列は鮮度を確認できない
      ため、保守的に True を返す（新鮮だと誤って言わない）。
    - `today` 省略時は当日。テストは today を注入して決定的に検証する（today() に依存しない）。
    - `threshold_days` は既定 7 日（暦日）。lead_lag 等で別境界を使う場合に上書きする。
    """
    if not as_of:
        return True
    try:
        d = datetime.date.fromisoformat(as_of)
    except ValueError:
        return True
    ref = today if today is not None else datetime.date.today()
    return (ref - d).days >= threshold_days
