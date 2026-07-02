"""services.notable.build_notable_candidates の合流ゲート検証（ADR-067）。

一時 SQLite に signals / daily_quotes / news / holdings / watchlist をスタブし、
「広い母集団は材料 2 次元以上・GC 単独は落ちる・出来高極増は単独例外・レーダー 1 次元」を検証する。
実 API・実 Webhook は叩かない（testing-strategy）。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from app.db import repo
from app.db.engine import get_engine
from app.services import notable

SIG_DATE = "2026-03-01"


def _stock(code: str, sector17: str, name: str) -> dict[str, Any]:
    return {
        "code": code,
        "company_name": name,
        "sector33_code": "3700",
        "sector17_code": sector17,
        "market_code": "0111",
        "is_etf": 0,
        "updated_at": "2026-03-01T00:00:00+00:00",
    }


def _signal(code: str, signal_type: str, score: float, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "date": SIG_DATE,
        "code": code,
        "signal_type": signal_type,
        "score": score,
        "payload": json.dumps(payload, ensure_ascii=False),
    }


def _quotes(code: str, closes: list[tuple[str, float]]) -> None:
    """daily_quotes に adj_close 系列を入れる（当日大幅変動の素）。"""
    rows = [
        {
            "code": code,
            "date": d,
            "open": c,
            "high": c,
            "low": c,
            "close": c,
            "volume": 1000.0,
            "adj_close": c,
        }
        for d, c in closes
    ]
    repo.upsert_daily_quotes(rows)


def _news(code: str, *, polarity: str, title: str = "見出し") -> None:
    from app.db.schema import news

    with get_engine().begin() as conn:
        conn.execute(
            news.insert().values(
                level="stock",
                code=code,
                source="news",
                url=f"https://x/{code}/{polarity}",
                title=title,
                summary="要約。",
                published_at="2026-03-01",
                fetched_at=datetime.now(UTC).isoformat(),
                extraction_status="summarized",
                polarity=polarity,
            )
        )


def _seed_holdings(*codes: str) -> None:
    from app.db.schema import holdings, portfolios

    with get_engine().begin() as conn:
        pid = conn.execute(
            portfolios.insert().values(name="メイン", created_at="2026-01-01T00:00:00+00:00")
        ).inserted_primary_key[0]
        for code in codes:
            conn.execute(
                holdings.insert().values(portfolio_id=pid, code=code, shares=100.0, avg_cost=1000.0)
            )


def _candidate_codes(result: dict[str, Any]) -> set[str]:
    return {c["code"] for c in result["candidates"]}


def test_gc_only_broad_stock_is_dropped(temp_db: None) -> None:
    """広い母集団で GC 単独（材料 1 次元）は候補にならない＝注目過多の発生源を落とす（ADR-067）。"""
    repo.upsert_stocks([_stock("72030", "6", "トヨタ自動車")])
    repo.upsert_signals(
        [_signal("72030", "momentum", 1.0, {"golden_cross": True, "notable": True, "label": "GC"})]
    )
    with get_engine().connect() as conn:
        result = notable.build_notable_candidates(conn)
    assert "72030" not in _candidate_codes(result)


def test_gc_plus_volume_is_candidate(temp_db: None) -> None:
    """GC（値動き）＋出来高急増（notable）＝材料 2 次元 → 候補になる。"""
    repo.upsert_stocks([_stock("72030", "6", "トヨタ自動車")])
    repo.upsert_signals(
        [
            _signal("72030", "momentum", 1.0, {"golden_cross": True, "notable": True}),
            _signal("72030", "volume_spike", 0.4, {"ratio": 4.0, "notable": True}),
        ]
    )
    with get_engine().connect() as conn:
        result = notable.build_notable_candidates(conn)
    codes = _candidate_codes(result)
    assert "72030" in codes
    cand = next(c for c in result["candidates"] if c["code"] == "72030")
    assert set(cand["materials"]) == {"price", "volume"}


def test_big_move_plus_news_is_candidate(temp_db: None) -> None:
    """当日大幅変動（+10%）＋悪材料ニュース＝材料 2 次元 → 候補（GC/RSI 反転無しでも値動き）。"""
    repo.upsert_stocks([_stock("67580", "8", "ソニーグループ")])
    # momentum シグナルは無いが大幅変動で値動き材料が立つ。
    _quotes("67580", [("2026-02-27", 100.0), ("2026-02-28", 110.0)])
    _news("67580", polarity="negative", title="ソニーに悪材料")
    # signals を最低 1 行入れて signal_date を確定させる（別銘柄の無関係シグナル）。
    repo.upsert_stocks([_stock("99999", "1", "ダミー")])
    repo.upsert_signals([_signal("99999", "momentum", 0.5, {"notable": False})])
    with get_engine().connect() as conn:
        result = notable.build_notable_candidates(conn)
    cand = next((c for c in result["candidates"] if c["code"] == "67580"), None)
    assert cand is not None
    assert set(cand["materials"]) == {"price", "news"}


def test_extreme_volume_alone_is_candidate(temp_db: None) -> None:
    """出来高極増（ratio>=7）は他に材料が無くても単独で候補（carve-out・ADR-067）。"""
    repo.upsert_stocks([_stock("60980", "16", "リクルート")])
    repo.upsert_signals([_signal("60980", "volume_spike", 0.9, {"ratio": 9.0, "notable": True})])
    with get_engine().connect() as conn:
        result = notable.build_notable_candidates(conn)
    assert "60980" in _candidate_codes(result)


def test_moderate_volume_alone_is_dropped(temp_db: None) -> None:
    """出来高急増でも極増未満（ratio 4.0）の単独は広い母集団では候補にならない。"""
    repo.upsert_stocks([_stock("60980", "16", "リクルート")])
    repo.upsert_signals([_signal("60980", "volume_spike", 0.4, {"ratio": 4.0, "notable": True})])
    with get_engine().connect() as conn:
        result = notable.build_notable_candidates(conn)
    assert "60980" not in _candidate_codes(result)


def test_radar_holding_with_single_material_is_candidate(temp_db: None) -> None:
    """保有銘柄は材料 1 次元（GC 単独）でも候補＝自分が気にしている銘柄（レーダー枠・ADR-067）。"""
    repo.upsert_stocks([_stock("72030", "6", "トヨタ自動車")])
    _seed_holdings("72030")
    repo.upsert_signals(
        [_signal("72030", "momentum", 1.0, {"golden_cross": True, "notable": True})]
    )
    with get_engine().connect() as conn:
        result = notable.build_notable_candidates(conn)
    cand = next((c for c in result["candidates"] if c["code"] == "72030"), None)
    assert cand is not None
    assert cand["in_radar"] is True


def test_radar_with_zero_material_is_dropped(temp_db: None) -> None:
    """保有でも当日に材料ゼロなら候補にしない（静かな日は黙る・ADR-067）。"""
    repo.upsert_stocks([_stock("72030", "6", "トヨタ自動車"), _stock("99999", "1", "ダミー")])
    _seed_holdings("72030")
    # signal_date を作るだけの無関係シグナル（72030 には何も無い）。
    repo.upsert_signals([_signal("99999", "momentum", 0.5, {"notable": False})])
    with get_engine().connect() as conn:
        result = notable.build_notable_candidates(conn)
    assert "72030" not in _candidate_codes(result)


def test_leadlag_leader_sector_counts_as_material(temp_db: None) -> None:
    """当日 lead_lag のリーダー業種に属する銘柄は、値動き材料と合わせて 2 次元で候補（材料④）。"""
    # S17 "1"（食品）→ TOPIX-17 ETF "1617" → signals.code "16170"。
    repo.upsert_stocks([_stock("28020", "1", "味の素")])
    repo.upsert_signals(
        [
            _signal("16170", "lead_lag", 0.85, {"label": "食品リーダー"}),  # 業種リーダー
            _signal("28020", "momentum", 1.0, {"golden_cross": True, "notable": True}),  # 値動き
        ]
    )
    with get_engine().connect() as conn:
        result = notable.build_notable_candidates(conn)
    cand = next((c for c in result["candidates"] if c["code"] == "28020"), None)
    assert cand is not None
    assert set(cand["materials"]) == {"price", "leadlag"}


def test_stealth_breakout_alone_is_candidate(temp_db: None) -> None:
    """ステルス仕込みの上放れ（phase=breakout・出来高確認あり）は単独で候補（carve-out・ADR-074）。"""
    repo.upsert_stocks([_stock("60980", "16", "リクルート")])
    repo.upsert_signals(
        [
            _signal(
                "60980",
                "stealth_accum",
                0.8,
                {"phase": "breakout", "volume_confirmed": True, "vol_elevation": 1.6},
            )
        ]
    )
    with get_engine().connect() as conn:
        result = notable.build_notable_candidates(conn)
    assert "60980" in _candidate_codes(result)


def test_stealth_in_range_alone_is_dropped(temp_db: None) -> None:
    """仕込み継続中（phase=in_range）の単独は広い母集団では候補にならない（合流ゲート維持・ADR-074）。"""
    repo.upsert_stocks([_stock("60980", "16", "リクルート")])
    repo.upsert_signals(
        [
            _signal(
                "60980",
                "stealth_accum",
                0.6,
                {"phase": "in_range", "volume_confirmed": False, "vol_elevation": 1.4},
            )
        ]
    )
    with get_engine().connect() as conn:
        result = notable.build_notable_candidates(conn)
    assert "60980" not in _candidate_codes(result)


def test_stealth_in_range_plus_price_is_candidate(temp_db: None) -> None:
    """仕込み継続（in_range）＋値動き（GC）＝材料 2 次元 → 候補（in_range も 1 次元）。"""
    repo.upsert_stocks([_stock("72030", "6", "トヨタ自動車")])
    repo.upsert_signals(
        [
            _signal("72030", "momentum", 1.0, {"golden_cross": True, "notable": True}),
            _signal(
                "72030",
                "stealth_accum",
                0.6,
                {"phase": "in_range", "volume_confirmed": False, "vol_elevation": 1.4},
            ),
        ]
    )
    with get_engine().connect() as conn:
        result = notable.build_notable_candidates(conn)
    cand = next((c for c in result["candidates"] if c["code"] == "72030"), None)
    assert cand is not None
    assert set(cand["materials"]) == {"price", "stealth"}


def test_counts_reported(temp_db: None) -> None:
    """counts に signals 総数・候補数が入る（digest 極薄サマリの素・ADR-067）。"""
    repo.upsert_stocks([_stock("72030", "6", "トヨタ自動車")])
    repo.upsert_signals(
        [
            _signal("72030", "momentum", 1.0, {"golden_cross": True, "notable": True}),
            _signal("72030", "volume_spike", 0.4, {"ratio": 4.0, "notable": True}),
        ]
    )
    with get_engine().connect() as conn:
        result = notable.build_notable_candidates(conn)
    assert result["counts"]["signals"] == 2
    assert result["counts"]["candidates"] == 1
