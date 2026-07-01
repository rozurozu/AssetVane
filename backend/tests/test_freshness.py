"""services.freshness の鮮度判定テスト（ADR-071）。

設計の真実: docs/decisions.md ADR-071（is_delayed を as_of vs today の鮮度実測に一元化）。
today を注入して datetime.date.today に依存せず決定的に検証する（DB にも触れない純関数）。
"""

from __future__ import annotations

import datetime

from app.services.freshness import is_delayed

_TODAY = datetime.date(2026, 7, 1)


def test_none_as_of_is_delayed_true() -> None:
    """as_of が None（データ無・未取得）は鮮度未確認なので保守的に True。"""
    assert is_delayed(None, _TODAY) is True


def test_unparseable_as_of_is_delayed_true() -> None:
    """parse 不能な as_of も鮮度を確認できないため保守的に True。"""
    assert is_delayed("not-a-date", _TODAY) is True


def test_today_is_fresh() -> None:
    """当日データは遅延なし（0 日差）。"""
    assert is_delayed("2026-07-01", _TODAY) is False


def test_six_days_old_is_fresh() -> None:
    """6 日前は閾値 7 日未満なので遅延なし（連休の隙間を誤検知しない境界の内側）。"""
    assert is_delayed("2026-06-25", _TODAY) is False


def test_seven_days_old_is_delayed() -> None:
    """7 日前ちょうどは閾値以上なので遅延あり（境界は >=）。"""
    assert is_delayed("2026-06-24", _TODAY) is True


def test_eight_days_old_is_delayed() -> None:
    """8 日前は遅延あり（有料プランでも夜間バッチが止まって stale なケースを捕まえる）。"""
    assert is_delayed("2026-06-23", _TODAY) is True


def test_free_plan_84_days_old_is_delayed() -> None:
    """Free プランは約 84 日遅れのデータしか無く、as_of 自体が古いので自動で True。

    プランを読まずとも鮮度だけで Free 遅延を捕まえられる（ADR-071 の核心）。
    """
    assert is_delayed("2026-04-08", _TODAY) is True  # 84 日前


def test_threshold_override() -> None:
    """threshold_days の上書きで境界を変えられる（lead_lag 等で 30 日を使う想定）。"""
    # 10 日前は既定 7 日なら遅延だが、閾値 30 日なら遅延なし。
    assert is_delayed("2026-06-21", _TODAY) is True
    assert is_delayed("2026-06-21", _TODAY, threshold_days=30) is False
