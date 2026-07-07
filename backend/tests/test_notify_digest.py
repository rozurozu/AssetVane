"""notify_digest の digest 組み立てと送信（phase6-spec.md §3・ADR-067）。

一時 SQLite に notable_picks / advisor_journal / policy / holdings+news をスタブし、AI 選別の表示・
表示上限・保有悪材料の決定論セクション（ADR-051 維持）・⑦リバランス・ALWAYS_DAILY_DIGEST=False の
スキップ・極薄サマリ・例外時 JobResult(ok=False) を検証する。実 Webhook は叩かない。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.batch.jobs import notify_digest
from app.config import settings
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
STOCK2 = {**STOCK, "code": "67580", "company_name": "ソニーグループ"}

TODAY = "2026-06-05"


def _seed_pick(code: str, reason: str, *, date: str = TODAY, source: str = "nightly") -> None:
    """notable_picks に AI 選別を 1 件入れる（夜AI の submit_notable_stocks 相当）。"""
    with get_engine().begin() as conn:
        repo.upsert_notable_pick(conn, date=date, code=code, reason=reason, source=source)


def _signal(code: str, signal_type: str, score: float, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "date": "2026-03-01",
        "code": code,
        "signal_type": signal_type,
        "score": score,
        "payload": json.dumps(payload, ensure_ascii=False),
    }


def test_build_digest_shows_ai_picks(temp_db: None) -> None:
    """AI 選別（notable_picks）が社名(コード) — 理由 で載る（ADR-067）。"""
    repo.upsert_stocks([STOCK, STOCK2])
    _seed_pick("72030", "出来高急増と GC が重なり反発の初動")
    _seed_pick("67580", "悪材料ニュースで急落、保有の点検が必要")

    with get_engine().connect() as conn:
        content = notify_digest.build_digest_content(conn, TODAY)

    assert content is not None
    assert "注目シグナル（AI 選別・2 件）" in content
    assert "トヨタ自動車 (72030) — 出来高急増と GC が重なり反発の初動" in content
    assert "ソニーグループ (67580) — 悪材料ニュースで急落、保有の点検が必要" in content


def test_build_digest_survives_candidates_recompute_failure(
    monkeypatch: pytest.MonkeyPatch, temp_db: None
) -> None:
    """#18: 候補 counts の再計算が失敗しても digest 本体（AI 選別・保有悪材料の安全網）は落ちない。

    build_notable_candidates はサマリ件数だけの best-effort。例外でも本文送信を巻き添えにしない。
    """
    repo.upsert_stocks([STOCK])
    _seed_pick("72030", "重なりで注目")  # 本文の実コンテンツ（has_content）

    def _boom(conn: Any) -> dict[str, Any]:
        raise RuntimeError("candidates down")

    monkeypatch.setattr(notify_digest, "build_notable_candidates", _boom)
    with get_engine().connect() as conn:
        content = notify_digest.build_digest_content(conn, TODAY)

    assert content is not None  # 巻き添えにならず送信対象は残る
    assert "72030" in content  # AI 選別（実コンテンツ）は出る
    assert "サマリ" in content  # サマリは 0/省略でも出る（クラッシュしない）


def test_build_digest_notable_cap_truncates(monkeypatch: pytest.MonkeyPatch, temp_db: None) -> None:
    """notable_digest_max を超える AI 選別は「…ほか N 件」で切る（ADR-067）。"""
    monkeypatch.setattr(settings, "notable_digest_max", 2)
    repo.upsert_stocks([STOCK])
    for i in range(5):
        _seed_pick(f"7203{i}", f"理由{i}")

    with get_engine().connect() as conn:
        content = notify_digest.build_digest_content(conn, TODAY)
    assert content is not None
    assert "…ほか 3 件" in content  # 5 件中 2 件表示・残り 3


def test_build_digest_no_picks_shows_none(temp_db: None) -> None:
    """AI 選別が無ければ「注目シグナル: なし」（旧 Top N 抽出は撤去・ADR-067）。"""
    with get_engine().connect() as conn:
        content = notify_digest.build_digest_content(conn, TODAY)
    assert content is not None
    assert "🔔 注目シグナル: なし" in content


def test_build_digest_shows_proposal(temp_db: None) -> None:
    """夜AI の proposal と方針変更案が載る（ADR-014）。"""
    with get_engine().begin() as conn:
        repo.insert_journal(
            conn,
            date=TODAY,
            source="nightly",
            proposal="現金比率を上げる検討",
            proposed_policy_change=json.dumps({"field": "target_cash_ratio", "to": 0.2}),
        )
    with get_engine().connect() as conn:
        content = notify_digest.build_digest_content(conn, TODAY)
    assert content is not None
    assert "現金比率を上げる検討" in content
    assert "target_cash_ratio → 0.2" in content


def test_build_digest_summary_counts(temp_db: None) -> None:
    """極薄サマリに signals/候補/AI 選別の件数が出る（ADR-067）。"""
    repo.upsert_stocks([STOCK])
    # 候補 1 件になる合流（GC＋出来高急増）。
    repo.upsert_signals(
        [
            _signal("72030", "momentum", 1.0, {"golden_cross": True, "notable": True}),
            _signal("72030", "volume_spike", 0.4, {"ratio": 4.0, "notable": True}),
        ]
    )
    _seed_pick("72030", "重なりで注目")

    with get_engine().connect() as conn:
        content = notify_digest.build_digest_content(conn, TODAY)
    assert content is not None
    assert "signals 2 件 / 候補 1 件 / AI 選別 1 件" in content


def test_build_digest_rebalance_alert(temp_db: None) -> None:
    """policy.updated_at が rebalance_alert_days 超ならリバランス行が出る。"""
    old = (datetime.now(UTC) - timedelta(days=settings.rebalance_alert_days + 5)).isoformat()
    with get_engine().begin() as conn:
        repo.upsert_policy(conn, {"risk_tolerance": "中", "updated_at": old})

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    with get_engine().connect() as conn:
        content = notify_digest.build_digest_content(conn, today)
    assert content is not None
    assert "リバランス" in content
    assert "方針を見直す時期" in content


def test_build_digest_skips_when_empty_and_not_always(
    monkeypatch: pytest.MonkeyPatch, temp_db: None
) -> None:
    """ALWAYS_DAILY_DIGEST=False かつ注目・⑦・提案・悪材料すべて無し → None（送らない）。"""
    monkeypatch.setattr(settings, "always_daily_digest", False)
    with get_engine().connect() as conn:
        content = notify_digest.build_digest_content(conn, TODAY)
    assert content is None


def test_build_digest_always_sends_summary_when_empty(
    monkeypatch: pytest.MonkeyPatch, temp_db: None
) -> None:
    """ALWAYS_DAILY_DIGEST=True（既定）なら検知ゼロでもサマリを返す（毎朝届く）。"""
    monkeypatch.setattr(settings, "always_daily_digest", True)
    with get_engine().connect() as conn:
        content = notify_digest.build_digest_content(conn, TODAY)
    assert content is not None
    assert "注目シグナル: なし" in content
    assert "— サマリ:" in content


def _pin_index_symbols(monkeypatch: pytest.MonkeyPatch) -> None:
    """digest の指数対象を 2 指数に固定（米国業種 ETF の自動追加を無効化）。"""
    from app.batch.jobs import fetch_index

    monkeypatch.setattr(settings, "index_symbols", "^SPX,^NKX")
    monkeypatch.setattr(fetch_index, "US_SECTOR_ETFS", ())
    monkeypatch.setattr(settings, "always_daily_digest", True)


def test_build_digest_includes_failed_index_line(
    monkeypatch: pytest.MonkeyPatch, temp_db: None
) -> None:
    """直近の取得試行が失敗した指数があれば digest に非アラートの情報行が出る。"""
    _pin_index_symbols(monkeypatch)

    repo.upsert_fetch_meta("index_quotes:^SPX", "2026-06-05")
    repo.upsert_fetch_meta("index_quotes:^NKX", "2026-06-04")
    repo.mark_fetch_attempt_failed("index_quotes:^NKX")

    with get_engine().connect() as conn:
        content = notify_digest.build_digest_content(conn, "2026-06-05")

    assert content is not None
    assert "取得できなかった指数" in content
    assert "^NKX（最終取得 2026-06-04）" in content
    failed_line = next(line for line in content.splitlines() if "取得できなかった指数" in line)
    assert "^SPX" not in failed_line


def test_build_digest_no_failed_line_when_all_ok(
    monkeypatch: pytest.MonkeyPatch, temp_db: None
) -> None:
    """全指数の直近試行が成功なら情報行は出ない（休場の空取得も成功扱い）。"""
    _pin_index_symbols(monkeypatch)

    repo.upsert_fetch_meta("index_quotes:^SPX", "2026-06-05")
    repo.upsert_fetch_meta("index_quotes:^NKX", "2026-06-05")

    with get_engine().connect() as conn:
        content = notify_digest.build_digest_content(conn, "2026-06-05")

    assert content is not None
    assert "取得できなかった指数" not in content


# ---------------------------------------------------------------------------
# ADR-066: AI Alpha Scorer のモデル未配置を情報行で可視化（沈黙にしない・failed_index 同型）
# ---------------------------------------------------------------------------


def test_build_digest_includes_ai_alpha_unconfigured_line(
    monkeypatch: pytest.MonkeyPatch, temp_db: None
) -> None:
    """AI Alpha モデル未配置（pkl 無し）なら digest に情報行が出る（沈黙にしない・ADR-066）。"""
    monkeypatch.setattr(notify_digest.model_store, "is_configured", lambda *a, **k: False)

    with get_engine().connect() as conn:
        content = notify_digest.build_digest_content(conn, TODAY)

    assert content is not None
    assert "AI決算スコア" in content
    assert "モデル未配置" in content


def test_build_digest_no_ai_alpha_line_when_configured(
    monkeypatch: pytest.MonkeyPatch, temp_db: None
) -> None:
    """モデル配置済みなら情報行は出ない（pkl を置けば自動で消える・ADR-066）。"""
    monkeypatch.setattr(notify_digest.model_store, "is_configured", lambda *a, **k: True)

    with get_engine().connect() as conn:
        content = notify_digest.build_digest_content(conn, TODAY)

    assert content is not None
    assert "AI決算スコア" not in content


def test_build_digest_ai_alpha_line_not_in_has_content(
    monkeypatch: pytest.MonkeyPatch, temp_db: None
) -> None:
    """AI Alpha 情報行は has_content に含めない（未配置だけの夜は digest を新規発火させない）。"""
    monkeypatch.setattr(settings, "always_daily_digest", False)
    monkeypatch.setattr(notify_digest.model_store, "is_configured", lambda *a, **k: False)

    with get_engine().connect() as conn:
        content = notify_digest.build_digest_content(conn, TODAY)

    assert content is None


# ---------------------------------------------------------------------------
# ADR-051: ②保有銘柄の悪材料アラート（決定論セクション・ADR-067 で維持）
# ---------------------------------------------------------------------------


def _insert_stock_news(
    url: str,
    code: str,
    *,
    title: str = "見出し",
    polarity: str | None = None,
    fetched_at: str | None = None,
    published_at: str = "2026-06-05",
) -> None:
    """stock 層 news を 1 行入れる（②の対象・fetched_at 既定は now＝24h 窓内）。"""
    from app.db.schema import news

    with get_engine().begin() as conn:
        conn.execute(
            news.insert().values(
                level="stock",
                code=code,
                source="news",
                url=url,
                title=title,
                summary="要約。",
                published_at=published_at,
                fetched_at=fetched_at or datetime.now(UTC).isoformat(),
                extraction_status="summarized",
                polarity=polarity,
            )
        )


def _seed_holdings(*codes: str) -> None:
    """既定ポートフォリオ＋保有を作る（②保有悪材料の対象・list_portfolios の先頭）。"""
    from app.db.schema import holdings, portfolios

    with get_engine().begin() as conn:
        pk = conn.execute(
            portfolios.insert().values(name="メイン", created_at="2026-01-01T00:00:00+00:00")
        ).inserted_primary_key
        assert pk is not None
        pid = pk[0]
        for code in codes:
            conn.execute(
                holdings.insert().values(portfolio_id=pid, code=code, shares=100.0, avg_cost=1000.0)
            )


def test_build_digest_holding_risk_section(temp_db: None) -> None:
    """②保有銘柄の悪材料（negative×24h 窓）がセクションで出て positive は出ない（ADR-051 維持）。"""
    repo.upsert_stocks([STOCK, STOCK2])
    _seed_holdings("72030", "67580")
    _insert_stock_news("https://x/neg", "72030", title="トヨタにリコール", polarity="negative")
    _insert_stock_news("https://x/pos", "67580", title="ソニー好決算", polarity="positive")

    with get_engine().connect() as conn:
        content = notify_digest.build_digest_content(conn, TODAY)
    assert content is not None
    assert "保有銘柄の悪材料" in content
    assert "トヨタにリコール" in content
    assert "ソニー好決算" not in content


def test_build_digest_no_risk_section_when_no_negative(temp_db: None) -> None:
    """悪材料が無ければ「保有銘柄の悪材料」セクションごと省略する（ADR-051）。"""
    repo.upsert_stocks([STOCK])
    _seed_holdings("72030")
    _insert_stock_news("https://x/pos", "72030", title="好材料", polarity="positive")

    with get_engine().connect() as conn:
        content = notify_digest.build_digest_content(conn, TODAY)
    assert content is not None
    assert "保有銘柄の悪材料" not in content


def test_build_digest_risk_triggers_send_when_not_always(
    monkeypatch: pytest.MonkeyPatch, temp_db: None
) -> None:
    """悪材料があれば always_daily_digest=False でも送る（has_content に含む・ADR-051）。"""
    monkeypatch.setattr(settings, "always_daily_digest", False)
    repo.upsert_stocks([STOCK])
    _seed_holdings("72030")
    _insert_stock_news("https://x/neg", "72030", title="トヨタ下方修正", polarity="negative")

    with get_engine().connect() as conn:
        content = notify_digest.build_digest_content(conn, TODAY)
    assert content is not None
    assert "トヨタ下方修正" in content


def test_build_digest_risk_excludes_out_of_window(temp_db: None) -> None:
    """fetched_at が 24h より前の悪材料は出ない（再掲なし・ADR-051）。"""
    repo.upsert_stocks([STOCK])
    _seed_holdings("72030")
    old = (datetime.now(UTC) - timedelta(hours=48)).isoformat()
    _insert_stock_news(
        "https://x/old", "72030", title="昨日の悪材料", polarity="negative", fetched_at=old
    )

    with get_engine().connect() as conn:
        content = notify_digest.build_digest_content(conn, TODAY)
    assert content is not None
    assert "昨日の悪材料" not in content


def test_run_sends_once_and_returns_jobresult(
    monkeypatch: pytest.MonkeyPatch, temp_db: None
) -> None:
    """run() は build → send_once で 1 通送り JobResult(ok=True) を返す。"""
    sent: list[tuple[str, str]] = []

    def _fake_send_once(notify_key: str, content: str, **_: Any) -> bool:
        sent.append((notify_key, content))
        return True

    monkeypatch.setattr(notify_digest.notify, "send_once", _fake_send_once)

    result = notify_digest.run()
    assert result.ok is True
    assert result.rows == 1
    assert len(sent) == 1
    assert sent[0][0].startswith("digest:")


def test_run_catches_exception(monkeypatch: pytest.MonkeyPatch, temp_db: None) -> None:
    """build で例外 → JobResult(ok=False)（runner が error 通知）。"""

    def _boom(conn: Any, today: str) -> None:
        raise RuntimeError("DB 障害")

    monkeypatch.setattr(notify_digest, "build_digest_content", _boom)
    result = notify_digest.run()
    assert result.ok is False
    assert "DB 障害" in result.detail


# ---------------------------------------------------------------------------
# ADR-088（#3）: ③保有の前提崩れの疑い（決定論・thesis-aware）
# ---------------------------------------------------------------------------


def _seed_quote(code: str, close: float, date: str = TODAY) -> None:
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


def _seed_buy_proposal(code: str, invalidation: str) -> None:
    with get_engine().begin() as conn:
        repo.insert_proposal(
            conn,
            created_date="2026-06-01",
            kind="buy",
            body=json.dumps(
                {
                    "code": code,
                    "company_name": "銘柄",
                    "market": "JP",
                    "invalidation": invalidation,
                },
                ensure_ascii=False,
            ),
            rationale="根拠",
            status="pending",
        )


def test_build_digest_thesis_watch_section(temp_db: None) -> None:
    """含み損＋記録済み thesis の保有が『前提崩れの疑い』セクションで出る（ADR-088）。"""
    repo.upsert_stocks([STOCK])
    _seed_holdings("72030")  # shares=100 / avg_cost=1000
    _seed_quote("72030", 700.0)  # -30% 含み損
    _seed_buy_proposal("72030", "営業利益が下方修正されたら")

    with get_engine().connect() as conn:
        content = notify_digest.build_digest_content(conn, TODAY)
    assert content is not None
    assert "保有の前提崩れの疑い" in content
    assert "72030" in content
    assert "前提: 営業利益が下方修正されたら" in content


def test_build_digest_thesis_watch_triggers_send_when_not_always(
    monkeypatch: pytest.MonkeyPatch, temp_db: None
) -> None:
    """前提崩れの疑いがあれば always_daily_digest=False でも送る（has_content に含む・ADR-088）。"""
    monkeypatch.setattr(settings, "always_daily_digest", False)
    repo.upsert_stocks([STOCK])
    _seed_holdings("72030")
    _seed_quote("72030", 700.0)
    _seed_buy_proposal("72030", "崩れたら")

    with get_engine().connect() as conn:
        content = notify_digest.build_digest_content(conn, TODAY)
    assert content is not None
    assert "保有の前提崩れの疑い" in content


def test_build_digest_thesis_watch_absent_for_news_only_without_thesis(temp_db: None) -> None:
    """thesis 無・生ニュース単独は #3 では鳴らさず ADR-051 の②だけに出る（二重掲載回避）。"""
    repo.upsert_stocks([STOCK])
    _seed_holdings("72030")
    _seed_quote("72030", 1000.0)  # 含み損なし（材料は news 1 次元のみ）
    _insert_stock_news("https://x/neg", "72030", title="トヨタにリコール", polarity="negative")

    with get_engine().connect() as conn:
        content = notify_digest.build_digest_content(conn, TODAY)
    assert content is not None
    assert "保有の前提崩れの疑い" not in content  # #3 は鳴らない
    assert "保有銘柄の悪材料" in content  # ADR-051 の②には出る
