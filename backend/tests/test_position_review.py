"""保有の前提崩れ監視ビルダー（ADR-088・#3）の受け入れテスト。

一時 SQLite に portfolios / stocks / daily_quotes / holdings / news / valuation_snapshots /
proposals をスタブし、build_position_reviews が前提崩れの疑いのある保有だけを thesis 添付で返す
こと、ADR-051 の生ニュース②と thesis-aware ゲートで差別化されること、cap/dropped・整形・
JSON-safe を検証する（testing-strategy・ネットに出ない）。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from app.config import settings
from app.db import repo
from app.db.engine import get_engine
from app.services.position_review import (
    build_position_reviews,
    format_position_reviews_for_prompt,
)


def _stock(code: str, name: str, sector33: str = "3700") -> dict[str, Any]:
    return {
        "code": code,
        "company_name": name,
        "sector33_code": sector33,
        "sector17_code": "6",
        "market_code": "0111",
        "is_etf": 0,
        "updated_at": "2026-01-01T00:00:00+00:00",
    }


def _seed_quote(code: str, close: float, date: str = "2026-06-05") -> None:
    """最新終値 1 本を入れる（value_holdings の含み損益計算用）。"""
    repo.upsert_daily_quotes(
        [
            {
                "code": code,
                "date": date,
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 1000.0,
                "adj_close": close,
            }
        ]
    )


def _seed_portfolio(holdings_spec: list[tuple[str, float, float]]) -> int:
    """既定ポートフォリオ＋保有（code, shares, avg_cost）を作り portfolio_id を返す。"""
    from app.db.schema import holdings, portfolios

    with get_engine().begin() as conn:
        pk = conn.execute(
            portfolios.insert().values(name="メイン", created_at="2026-01-01T00:00:00+00:00")
        ).inserted_primary_key
        assert pk is not None
        pid = pk[0]
        for code, shares, avg_cost in holdings_spec:
            conn.execute(
                holdings.insert().values(
                    portfolio_id=pid, code=code, shares=shares, avg_cost=avg_cost
                )
            )
    return int(pid)


def _seed_buy_proposal(
    code: str,
    *,
    invalidation: str | None = None,
    conviction: str | None = None,
    catalyst: str | None = None,
    created_date: str = "2026-06-01",
) -> None:
    """記録済み thesis（買い提案 body・ADR-084）を 1 件入れる。"""
    body: dict[str, Any] = {"code": code, "company_name": "銘柄", "market": "JP"}
    if conviction is not None:
        body["conviction"] = conviction
    if invalidation is not None:
        body["invalidation"] = invalidation
    if catalyst is not None:
        body["catalyst"] = catalyst
    with get_engine().begin() as conn:
        repo.insert_proposal(
            conn,
            created_date=created_date,
            kind="buy",
            body=json.dumps(body, ensure_ascii=False),
            rationale="買いの根拠",
            status="pending",
        )


def _insert_stock_news(code: str, *, title: str, polarity: str = "negative") -> None:
    from app.db.schema import news

    with get_engine().begin() as conn:
        conn.execute(
            news.insert().values(
                level="stock",
                code=code,
                source="news",
                url=f"https://x/{code}/{title}",
                title=title,
                summary="要約。",
                published_at="2026-06-05",
                fetched_at=datetime.now(UTC).isoformat(),  # 24h 窓内
                extraction_status="summarized",
                polarity=polarity,
            )
        )


def _seed_valuation(code: str, **cols: Any) -> None:
    repo.upsert_valuation_snapshots([{"code": code, "as_of_date": "2026-06-05", **cols}])


def _reviews(pid: int | None = None) -> dict[str, Any]:
    with get_engine().connect() as conn:
        return build_position_reviews(conn, portfolio_id=pid)


# ---------------------------------------------------------------------------
# 材料フラグ × thesis ゲート
# ---------------------------------------------------------------------------


def test_loss_with_thesis_flags(temp_db: None) -> None:
    """含み損（entry 比 -20%）＋記録済み thesis → needs_review・thesis 3 属性が入る。"""
    repo.upsert_stocks([_stock("72030", "トヨタ")])
    _seed_quote("72030", 800.0)  # 簿価 1000 に対し -20%
    pid = _seed_portfolio([("72030", 100.0, 1000.0)])
    _seed_buy_proposal(
        "72030", conviction="high", invalidation="営業利益が下方修正されたら", catalyst="次期決算"
    )

    result = _reviews(pid)
    assert result["counts"]["flagged"] == 1
    review = result["reviews"][0]
    assert review["code"] == "72030"
    assert review["needs_review"] is True
    assert "loss" in review["flags"]
    assert review["unrealized_pnl_pct"] == -0.2
    assert review["thesis"]["conviction"] == "high"
    assert review["thesis"]["invalidation"] == "営業利益が下方修正されたら"
    assert review["thesis"]["catalyst"] == "次期決算"


def test_negative_news_with_thesis_flags(temp_db: None) -> None:
    """含み損なし（±0）でも negative news＋thesis なら flagged（news 材料 1・thesis で通過）。"""
    repo.upsert_stocks([_stock("72030", "トヨタ")])
    _seed_quote("72030", 1000.0)  # 簿価と同値＝含み損なし
    pid = _seed_portfolio([("72030", 100.0, 1000.0)])
    _seed_buy_proposal("72030", invalidation="リコールが出たら")
    _insert_stock_news("72030", title="トヨタにリコール")

    result = _reviews(pid)
    assert result["counts"]["flagged"] == 1
    flags = result["reviews"][0]["flags"]
    assert "news" in flags
    assert "loss" not in flags


def test_guidance_miss_with_thesis_flags(temp_db: None) -> None:
    """会社予想の達成率 < 0.9（未達）＋thesis → guidance_miss で flagged（ADR-063 #4）。"""
    repo.upsert_stocks([_stock("72030", "トヨタ")])
    _seed_quote("72030", 1000.0)
    pid = _seed_portfolio([("72030", 100.0, 1000.0)])
    _seed_valuation("72030", op_forecast_achievement=0.7)
    _seed_buy_proposal("72030", invalidation="通期未達なら")

    result = _reviews(pid)
    assert result["counts"]["flagged"] == 1
    assert "guidance_miss" in result["reviews"][0]["flags"]


def test_healthy_holding_not_flagged(temp_db: None) -> None:
    """含み益・悪材料なし・予想未達なしなら thesis があっても flagged にならない。"""
    repo.upsert_stocks([_stock("72030", "トヨタ")])
    _seed_quote("72030", 1200.0)  # +20% 含み益
    pid = _seed_portfolio([("72030", 100.0, 1000.0)])
    _seed_buy_proposal("72030", invalidation="崩れたら")

    result = _reviews(pid)
    assert result["counts"]["flagged"] == 0
    assert result["reviews"] == []


def test_single_material_without_thesis_not_flagged(temp_db: None) -> None:
    """thesis 無・材料 1（生ニュース単独）は #3 では鳴らさない（ADR-051 の②と差別化）。"""
    repo.upsert_stocks([_stock("72030", "トヨタ")])
    _seed_quote("72030", 1000.0)  # 含み損なし
    pid = _seed_portfolio([("72030", 100.0, 1000.0)])
    _insert_stock_news("72030", title="トヨタにリコール")  # 材料 1・thesis 無

    result = _reviews(pid)
    assert result["counts"]["flagged"] == 0
    assert result["reviews"] == []


def test_two_materials_without_thesis_flags(temp_db: None) -> None:
    """thesis 無でも材料 2 次元（含み損＋悪材料）なら flagged になる。"""
    repo.upsert_stocks([_stock("72030", "トヨタ")])
    _seed_quote("72030", 800.0)  # -20% 含み損
    pid = _seed_portfolio([("72030", 100.0, 1000.0)])
    _insert_stock_news("72030", title="トヨタにリコール")

    result = _reviews(pid)
    assert result["counts"]["flagged"] == 1
    review = result["reviews"][0]
    assert review["n_flags"] == 2
    assert review["thesis"] is None


def test_cap_truncates_and_counts_dropped(temp_db: None) -> None:
    """position_review_cap を超える flagged は cap で切り、超過は counts.dropped に残す。"""
    settings_cap = settings.position_review_cap
    try:
        settings.position_review_cap = 1
        repo.upsert_stocks([_stock("72030", "トヨタ"), _stock("67580", "ソニー", "3600")])
        _seed_quote("72030", 700.0)
        _seed_quote("67580", 700.0)
        pid = _seed_portfolio([("72030", 100.0, 1000.0), ("67580", 100.0, 1000.0)])
        _seed_buy_proposal("72030", invalidation="崩れたら")
        _seed_buy_proposal("67580", invalidation="崩れたら")

        result = _reviews(pid)
        assert result["counts"]["flagged"] == 2
        assert len(result["reviews"]) == 1
        assert result["counts"]["dropped"] == 1
    finally:
        settings.position_review_cap = settings_cap


def test_no_holdings_returns_empty(temp_db: None) -> None:
    """保有ゼロ（ポートフォリオはある）でも落ちず空 reviews を返す。"""
    _seed_portfolio([])
    result = _reviews()
    assert result["reviews"] == []
    assert result["counts"]["holdings"] == 0


def test_no_portfolio_returns_empty(temp_db: None) -> None:
    """ポートフォリオ未作成でも落ちず空を返す（portfolio_id=None）。"""
    result = _reviews()
    assert result["portfolio_id"] is None
    assert result["reviews"] == []


# ---------------------------------------------------------------------------
# 整形・JSON-safe
# ---------------------------------------------------------------------------


def test_format_for_prompt(temp_db: None) -> None:
    """flagged 保有はプロンプト用文字列に社名(コード)＋前提崩れ条件で載る。"""
    repo.upsert_stocks([_stock("72030", "トヨタ")])
    _seed_quote("72030", 800.0)
    pid = _seed_portfolio([("72030", 100.0, 1000.0)])
    _seed_buy_proposal("72030", invalidation="営業利益が下方修正されたら")

    text = format_position_reviews_for_prompt(_reviews(pid))
    assert "トヨタ (72030)" in text
    assert "前提崩れ条件: 営業利益が下方修正されたら" in text


def test_format_for_prompt_empty() -> None:
    """flagged ゼロは『保有の前提崩れ: なし』の 1 行（空注入しない）。"""
    text = format_position_reviews_for_prompt(
        {"reviews": [], "counts": {"holdings": 0, "flagged": 0, "dropped": 0}}
    )
    assert "保有の前提崩れ: なし" in text


def test_result_is_json_safe(temp_db: None) -> None:
    """build_position_reviews の結果はそのまま json.dumps できる（Decimal/numpy を漏らさない）。"""
    repo.upsert_stocks([_stock("72030", "トヨタ")])
    _seed_quote("72030", 800.0)
    pid = _seed_portfolio([("72030", 100.0, 1000.0)])
    _seed_buy_proposal("72030", invalidation="崩れたら")
    json.dumps(_reviews(pid))  # 例外が出なければ JSON-safe
