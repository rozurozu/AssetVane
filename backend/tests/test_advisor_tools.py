"""Tool Calling 層・LLM アダプタ拡張のテスト（phase3-spec.md §4・§7・§10）。

DB は temp_db（一時 SQLite）、LLM・ネットは必ずモック（ネットを叩かない）。検証対象:
- openai_tools(phase) の Phase ゲート（min_phase 超は出さない）。
- handler の薄い橋渡し（検証→実関数→dict）と例外時 {"error": ...}。
- compute_indicators の既知系列（sma/rsi 妥当・データ不足で None）。
- complete のコストガード（block で CostGuardError・warn で続行・usage 計上）。
"""

from __future__ import annotations

import asyncio
import datetime
from typing import Any

import pandas as pd
import pytest

from app.advisor.tools import handlers
from app.advisor.tools.registry import CURRENT_PHASE, REGISTRY, openai_tools
from app.quant.indicators import compute_indicators
from app.services.llm_config import ResolvedFace

# ---------------------------------------------------------------------------
# Phase ゲート（openai_tools）
# ---------------------------------------------------------------------------


def test_openai_tools_phase1_only_p1_tools() -> None:
    """openai_tools(1) は P1 Tool だけ（metrics/optimize/submit_journal を含まない）。

    注目候補の合流ゲート系（get_notable_candidates / submit_notable_stocks・ADR-067）は
    signals 世代の機能なので min_phase=1（Phase 1 から露出）。
    """
    names = {t["function"]["name"] for t in openai_tools(1)}  # type: ignore[index]
    assert names == {
        "get_indicators",
        "get_signals",
        "get_method_card",
        # ADR-077: 過去提案の市場結果採点の成績取得（min_phase=1）。
        "get_track_record",
        # ADR-078: 判断ログ横断想起（FTS5 recall・min_phase=1）。
        "search_judgments",
        "get_notable_candidates",
        "screen_stocks",
        "submit_notable_stocks",
    }


def test_openai_tools_phase3_includes_submit_journal() -> None:
    """openai_tools(3) は submit_journal（min_phase=3）まで含む。"""
    names = {t["function"]["name"] for t in openai_tools(3)}  # type: ignore[index]
    assert "submit_journal" in names
    assert "get_portfolio_metrics" in names  # P2 も含む
    # dossier 系（P4）は登録自体しない。
    assert "get_dossier" not in names


def test_openai_tools_default_is_current_phase() -> None:
    """既定引数は CURRENT_PHASE（=7・Phase 7 リードラグまで露出）。"""
    assert CURRENT_PHASE == 7
    assert {t["function"]["name"] for t in openai_tools()} == {  # type: ignore[index]
        t["function"]["name"]  # type: ignore[index]
        for t in openai_tools(CURRENT_PHASE)
    }


def test_openai_tools_shape() -> None:
    """各要素は {type: function, function: {name, description, parameters}} 形。"""
    for tool in openai_tools(3):
        assert tool["type"] == "function"
        fn = tool["function"]  # type: ignore[index]
        assert set(fn) == {"name", "description", "parameters"}  # type: ignore[arg-type]


def test_registry_handlers_are_registered() -> None:
    """REGISTRY の各 handler が handlers.py の関数に紐づく。"""
    expected = {
        "get_indicators",
        "get_signals",
        # ADR-075: 手法カードのオンデマンド取得（min_phase=1）。
        "get_method_card",
        # ADR-077: 過去提案の市場結果採点の成績取得（min_phase=1）。
        "get_track_record",
        # ADR-078: 判断ログ横断想起（FTS5 recall・min_phase=1）。
        "search_judgments",
        # ADR-067: 注目候補の合流ゲート＋AI 選別（min_phase=1）。
        "get_notable_candidates",
        "submit_notable_stocks",
        "screen_stocks",
        "get_portfolio_metrics",
        "optimize_portfolio",
        # ADR-085: ポジションサイズ／ポートフォリオ影響 what-if（min_phase=2）。
        "simulate_trade_impact",
        # ADR-088: 保有の前提崩れ監視（min_phase=2）。
        "get_position_reviews",
        "get_financials",
        # ADR-048: バリュエーション判断（min_phase=2）。
        "get_valuation",
        "screen_valuation",
        "get_asset_overview",
        # ADR-054: 投信保有（min_phase=4）。
        "get_fund_holdings",
        "submit_journal",
        # Phase 4（Stock Dossier）。露出は min_phase=4 ゲートで制御（CURRENT_PHASE=4 で露出）。
        "get_dossier",
        "investigate_stock",
        "fetch_news",
        # ADR-034: 一般ニュース（min_phase=4）。
        "get_general_news",
        # ADR-044: ニュース3層文脈（min_phase=4）。
        "get_news_context",
        # ADR-052: ニュース起点 buy/sell 提案の起票（min_phase=4）。
        "propose_trade",
        # ADR-080: チャットからのウォッチ候補提示（min_phase=4・検証 only）。
        "propose_watchlist",
        # ADR-045: ニュース意味検索（min_phase=4）。
        "search_news",
        # ADR-062: 知識カード意味検索（min_phase=4）。
        "search_cards",
        # ADR-062 追補: チャットからのカード整備（min_phase=4・承認制）。
        "propose_card",
        "adjust_card_weight",
        # ADR-082: 投資家プロファイルの傾向メモ（profiler 面専用・allowlist_only）。
        "propose_profile_note",
        # ADR-086: 提案前 red-team 反証（skeptic 面専用・allowlist_only）。
        "submit_refutation",
        # Phase 7: 日米業種リードラグ（min_phase=7）。
        "get_lead_lag",
        # Phase 7(B-1): 米国株バリュエーション（min_phase=7・ADR-039/048/055）。
        "get_us_valuation",
        "screen_us_valuation",
        # ADR-050 段階A: テーマタグ（min_phase=7）。
        "list_themes",
        "get_stock_themes",
        "screen_by_theme",
        # Phase 7(B-2): 米株保有・FX 換算（min_phase=7・ADR-039/057）。
        "get_us_holdings",
    }
    assert set(REGISTRY) == expected


# ---------------------------------------------------------------------------
# Phase 4（Stock Dossier）— Phase ゲート・handler 橋渡し（spec §4・§8）
# ---------------------------------------------------------------------------


def test_openai_tools_phase3_hides_dossier_tools() -> None:
    """available_phase=3 では dossier 系 3 Tool が露出しない（min_phase=4 ゲート・spec §4）。"""
    names = {t["function"]["name"] for t in openai_tools(3)}  # type: ignore[index]
    assert "get_dossier" not in names
    assert "investigate_stock" not in names
    assert "fetch_news" not in names


def test_openai_tools_phase4_exposes_dossier_tools() -> None:
    """available_phase>=4 で dossier 系 3 Tool が露出する（Phase ゲート・spec §4）。"""
    names = {t["function"]["name"] for t in openai_tools(4)}  # type: ignore[index]
    assert {"get_dossier", "investigate_stock", "fetch_news"} <= names
    # P1〜P3 も引き続き含む（上位 phase は下位を内包する）。
    assert "get_indicators" in names and "submit_journal" in names
    # min_phase=7 の get_lead_lag は phase 4 では露出しない。
    assert "get_lead_lag" not in names


def test_openai_tools_phase7_exposes_lead_lag() -> None:
    """available_phase>=7 で get_lead_lag（min_phase=7）が露出する（Phase ゲート）。"""
    names = {t["function"]["name"] for t in openai_tools(7)}  # type: ignore[index]
    assert "get_lead_lag" in names
    # 下位 phase の Tool も内包する。
    assert {"get_general_news", "get_dossier", "get_indicators"} <= names


class _FakeBeginConn:
    """begin() 用の偽 conn（with でそのまま返す・commit はしない）。"""

    def __enter__(self) -> _FakeBeginConn:
        return self

    def __exit__(self, *_: Any) -> None:
        return None


class _FakeConnectConn:
    """connect() 用の偽 conn（with でそのまま返す）。"""

    def __enter__(self) -> _FakeConnectConn:
        return self

    def __exit__(self, *_: Any) -> None:
        return None


def test_handle_investigate_stock_bridges(monkeypatch: pytest.MonkeyPatch) -> None:
    """handle_investigate_stock: begin() で束ね investigate_stock を code のみで呼ぶ（mode 廃止）。

    mode は廃止（ADR-020 改訂）。
    """
    monkeypatch.setattr(handlers, "get_engine", lambda: type("E", (), {"begin": _FakeBeginConn})())
    captured: dict[str, Any] = {}

    async def _fake_investigate(conn: Any, code: str) -> dict[str, Any]:
        captured["code"] = code
        return {
            "code": code,
            "summary_md": "本文",
            "key_facts": "{}",
            "last_investigated_at": "2026-06-05T00:00:00+00:00",
            "n_sources_added": 2,
        }

    monkeypatch.setattr(handlers, "investigate_stock", _fake_investigate)
    out = _run(handlers.handle_investigate_stock({"code": "7203"}))
    assert captured == {"code": "7203"}  # mode は廃止（ADR-020 改訂）＝code のみで呼ぶ
    assert out["n_sources_added"] == 2
    assert out["summary_md"] == "本文"


def test_handle_investigate_stock_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """例外時は {"error": ...} を返しループを落とさない（spec §4）。"""
    monkeypatch.setattr(handlers, "get_engine", lambda: type("E", (), {"begin": _FakeBeginConn})())

    async def _boom(conn: Any, code: str) -> dict[str, Any]:
        raise RuntimeError("調査失敗")

    monkeypatch.setattr(handlers, "investigate_stock", _boom)
    out = _run(handlers.handle_investigate_stock({"code": "7203"}))
    assert "error" in out


def test_handle_get_dossier_composes(monkeypatch: pytest.MonkeyPatch) -> None:
    """handle_get_dossier: dossier 本体＋sources を合成・key_facts は obj 化（spec §4/§5.2）。"""
    monkeypatch.setattr(
        handlers, "get_engine", lambda: type("E", (), {"connect": _FakeConnectConn})()
    )
    monkeypatch.setattr(
        handlers.repo,
        "get_dossier",
        lambda conn, code: {
            "code": code,
            "summary_md": "## レポート",
            "key_facts": '{"per": 15.2, "topic": "増配"}',  # 生 TEXT
            "last_investigated_at": "2026-06-01T00:00:00+00:00",
            "updated_at": "2026-06-01T00:00:00+00:00",
        },
    )
    # ADR-044: ニュース源は統合コーパス news（level='stock'）を引く。news の source 列を
    # Tool 返却の source_type にマップする（キー名は不変）。
    monkeypatch.setattr(
        handlers.repo,
        "list_news",
        lambda conn, *, level, code, **_: [
            {
                "id": 1,
                "level": "stock",
                "code": code,
                "source": "news",  # 統合 news の source 列（旧 source_type）
                "url": "https://example.com/a",
                "title": "好決算",
                "summary": "増収増益",
                "published_at": "2026-06-01",
                "fetched_at": "2026-06-01T00:00:00+00:00",
            }
        ],
    )
    out = _run(handlers.handle_get_dossier({"code": "7203"}))
    assert out["code"] == "7203"
    assert out["summary_md"] == "## レポート"
    # key_facts は json.loads でオブジェクトになる（生 TEXT のままではない）。
    assert out["key_facts"] == {"per": 15.2, "topic": "増配"}
    # sources は spec §4 のキー subset（id/processed_at は出さない）。
    assert out["sources"] == [
        {
            "url": "https://example.com/a",
            "title": "好決算",
            "summary": "増収増益",
            "published_at": "2026-06-01",
            "source_type": "news",
        }
    ]


def test_handle_get_dossier_uninvestigated(monkeypatch: pytest.MonkeyPatch) -> None:
    """未調査（get_dossier=None）は summary_md="" ＋ key_facts=None で返す（spec §5.2）。"""
    monkeypatch.setattr(
        handlers, "get_engine", lambda: type("E", (), {"connect": _FakeConnectConn})()
    )
    monkeypatch.setattr(handlers.repo, "get_dossier", lambda conn, code: None)
    monkeypatch.setattr(handlers.repo, "list_news", lambda conn, **_: [])
    out = _run(handlers.handle_get_dossier({"code": "9999"}))
    assert out["summary_md"] == ""
    assert out["key_facts"] is None
    assert out["last_investigated_at"] is None
    assert out["sources"] == []


def test_handle_get_dossier_error_no_code() -> None:
    """code 欠落は {"error": ...}（境界で弾く・ループは落とさない）。"""
    out = _run(handlers.handle_get_dossier({}))
    assert "error" in out


# ---------------------------------------------------------------------------
# ADR-044: get_news_context（3層文脈）・既存ニュース Tool の張り替え回帰
# ---------------------------------------------------------------------------


def test_handle_get_news_context_bridges(monkeypatch: pytest.MonkeyPatch) -> None:
    """handle_get_news_context: build_news_context を呼び3層 dict を返す（ADR-044）。"""
    monkeypatch.setattr(
        handlers, "get_engine", lambda: type("E", (), {"connect": _FakeConnectConn})()
    )
    captured: dict[str, Any] = {}

    def _fake_build(conn: Any, code: str) -> dict[str, Any]:
        captured["code"] = code
        return {
            "code": code,
            "company_name": "トヨタ自動車",
            "sector17_code": "6",  # S17 体系（ADR-053・現実値に揃える）
            "sector_label": "自動車・輸送機",
            "stock": [
                {
                    "url": "u1",
                    "title": "好決算",
                    "summary": "s",
                    "published_at": "2026-06-01",
                    "source": "news",
                }
            ],
            "sector": [],
            "market": [
                {
                    "url": "u2",
                    "title": "金利",
                    "summary": "s",
                    "published_at": "2026-06-01",
                    "source": "news",
                }
            ],
        }

    monkeypatch.setattr(handlers, "build_news_context", _fake_build)
    out = _run(handlers.handle_get_news_context({"code": "7203"}))
    assert captured == {"code": "7203"}
    # 3 層キーが揃って返る（橋渡しはそのまま通す）。
    assert {"stock", "sector", "market"} <= set(out)
    assert out["sector_label"] == "自動車・輸送機"


def test_handle_get_news_context_error_no_code() -> None:
    """code 欠落は {"error": ...}（境界で弾く・ループは落とさない）。"""
    out = _run(handlers.handle_get_news_context({}))
    assert "error" in out


def test_handle_get_general_news_regression(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_general_news の張り替え後も従来形（categories グルーピング）を返す（ADR-044）。"""
    monkeypatch.setattr(
        handlers, "get_engine", lambda: type("E", (), {"connect": _FakeConnectConn})()
    )
    captured: dict[str, Any] = {}

    def _fake_list_news(conn: Any, *, level: str, since: str, **_: Any) -> list[dict[str, Any]]:
        captured["level"] = level  # market 層だけを引く（ADR-044 の張り替え）
        return [
            {
                "category": "市況",
                "url": "u1",
                "title": "株高",
                "summary": "s",
                "published_at": "2026-06-06",
            },
            {
                "category": "市況",
                "url": "u2",
                "title": "続伸",
                "summary": "s",
                "published_at": "2026-06-06",
            },
            {
                "category": "マクロ",
                "url": "u3",
                "title": "金利",
                "summary": "s",
                "published_at": "2026-06-06",
            },
        ]

    monkeypatch.setattr(handlers.repo, "list_news", _fake_list_news)
    out = _run(handlers.handle_get_general_news({}))
    assert captured["level"] == "market"
    cats = {c["label"]: c["items"] for c in out["categories"]}
    assert set(cats) == {"市況", "マクロ"}
    assert len(cats["市況"]) == 2
    # 記事は url/title/summary/published_at の subset（従来形・category は items に出さない）。
    assert set(cats["市況"][0]) == {"url", "title", "summary", "published_at"}


def test_handle_fetch_news_bridges(monkeypatch: pytest.MonkeyPatch) -> None:
    """handle_fetch_news: 自前 conn で社名解決し fetch_news(code, company_name, since=...) を呼ぶ。

    mode は廃止（ADR-020 改訂）。adapter は DB に触らない契約なので社名は handler が
    repo.get_stock から解決して渡す。
    """
    monkeypatch.setattr(
        handlers, "get_engine", lambda: type("E", (), {"connect": _FakeConnectConn})()
    )
    monkeypatch.setattr(
        handlers.repo, "get_stock", lambda conn, code: {"code": code, "company_name": "ソニーG"}
    )
    captured: dict[str, Any] = {}

    async def _fake_fetch(code: str, company_name: str, *, since: Any = None) -> list[dict]:
        captured["code"] = code
        captured["company_name"] = company_name
        captured["since"] = since
        return [{"url": "https://example.com/x", "title": "t"}]

    monkeypatch.setattr(handlers, "fetch_news", _fake_fetch)
    out = _run(handlers.handle_fetch_news({"code": "6758", "since": "2026-06-01"}))
    assert captured == {"code": "6758", "company_name": "ソニーG", "since": "2026-06-01"}
    assert out == {"code": "6758", "articles": [{"url": "https://example.com/x", "title": "t"}]}


def test_handle_fetch_news_falls_back_to_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """社名が引けない（get_stock=None）ときは code を社名代わりに渡す（空振りで落とさない）。"""
    monkeypatch.setattr(
        handlers, "get_engine", lambda: type("E", (), {"connect": _FakeConnectConn})()
    )
    monkeypatch.setattr(handlers.repo, "get_stock", lambda conn, code: None)
    captured: dict[str, Any] = {}

    async def _empty(code: str, company_name: str, *, since: Any = None) -> list[dict]:
        captured["company_name"] = company_name
        return []

    monkeypatch.setattr(handlers, "fetch_news", _empty)
    out = _run(handlers.handle_fetch_news({"code": "6758"}))
    assert captured["company_name"] == "6758"  # 社名が無ければ code を代用
    assert out == {"code": "6758", "articles": []}


def test_handle_fetch_news_error_no_code() -> None:
    """code 欠落は {"error": ...}。"""
    out = _run(handlers.handle_fetch_news({}))
    assert "error" in out


# ---------------------------------------------------------------------------
# compute_indicators（既知系列・データ不足）
# ---------------------------------------------------------------------------


def test_compute_indicators_known_series() -> None:
    """上昇系列で sma25<sma75 が崩れ、各指標が妥当な範囲に入る。"""
    n = 120
    df = pd.DataFrame(
        {
            "date": [f"2025-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(n)],
            "adj_close": [100.0 + i for i in range(n)],  # 単調増加
            "volume": [1000.0 + i for i in range(n)],
        }
    )
    out = compute_indicators(df)
    assert out["adj_close"] == pytest.approx(219.0)
    # 単調増加なので短期 SMA > 長期 SMA。
    assert out["sma25"] is not None and out["sma75"] is not None
    assert out["sma25"] > out["sma75"]
    # 連騰 → RSI は 100 付近（上端）。
    assert out["rsi14"] == pytest.approx(100.0, abs=1e-6)
    assert out["vol_ma20"] is not None
    assert out["as_of"] == df["date"].iloc[-1]


def test_compute_indicators_insufficient_data_none() -> None:
    """データ不足（30 日）では sma75 が None（数字を作らない＝ADR-014）。"""
    df = pd.DataFrame(
        {
            "date": [f"2025-01-{i + 1:02d}" for i in range(30)],
            "adj_close": [float(100 + i) for i in range(30)],
        }
    )
    out = compute_indicators(df)
    assert out["sma25"] is not None  # 25 日は足りる
    assert out["sma75"] is None  # 75 日に足りない
    assert out["vol_ma20"] is None  # volume 列が無い


def test_compute_indicators_empty() -> None:
    """空 DataFrame は全 None。"""
    out = compute_indicators(pd.DataFrame())
    assert all(out[k] is None for k in out)


# ---------------------------------------------------------------------------
# handler の橋渡し（quant/data をモック）
# ---------------------------------------------------------------------------


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _fake_conn_engine() -> Any:
    """handler 内 `with get_engine().connect() as conn:` を満たすダミー engine。"""

    class _FakeConn:
        def __enter__(self) -> _FakeConn:
            return self

        def __exit__(self, *_: Any) -> None:
            return None

    return type("E", (), {"connect": _FakeConn})()


def test_handle_get_indicators_bridges(monkeypatch: pytest.MonkeyPatch) -> None:
    """handle_get_indicators: 古い as_of は鮮度判定で is_delayed=True になる（ADR-071）。"""
    monkeypatch.setattr(handlers, "get_engine", _fake_conn_engine)
    monkeypatch.setattr(handlers.repo, "get_quotes", lambda conn, code: [{"date": "2025-01-01"}])
    monkeypatch.setattr(
        handlers,
        "compute_indicators",
        lambda df: {
            "as_of": "2025-01-01",  # 十分に古い → 遅延あり
            "adj_close": 100.0,
            "sma25": 1.0,
            "sma75": 2.0,
            "rsi14": 50.0,
            "vol_ma20": 9.0,
        },
    )
    out = _run(handlers.handle_get_indicators({"code": "7203"}))
    assert out["code"] == "7203"
    assert out["sma25"] == 1.0
    assert out["is_delayed"] is True


def test_handle_get_indicators_fresh_not_delayed(monkeypatch: pytest.MonkeyPatch) -> None:
    """当日の as_of なら is_delayed=False（鮮度実測・ADR-071）。

    _IS_DELAYED=True 固定だった旧実装ではここが必ず True になり誤って「遅延あり」と出ていた。
    handler が as_of を freshness.is_delayed に正しく渡していることを配線ごと検証する。
    """
    monkeypatch.setattr(handlers, "get_engine", _fake_conn_engine)
    today = datetime.date.today().isoformat()
    monkeypatch.setattr(handlers.repo, "get_quotes", lambda conn, code: [{"date": today}])
    monkeypatch.setattr(
        handlers,
        "compute_indicators",
        lambda df: {
            "as_of": today,  # 当日 → 遅延なし
            "adj_close": 100.0,
            "sma25": 1.0,
            "sma75": 2.0,
            "rsi14": 50.0,
            "vol_ma20": 9.0,
        },
    )
    out = _run(handlers.handle_get_indicators({"code": "7203"}))
    assert out["as_of"] == today
    assert out["is_delayed"] is False


def test_handle_get_indicators_validation_error() -> None:
    """引数欠落（code 無し）は {"error": ...} を返す（ループを落とさない）。"""
    out = _run(handlers.handle_get_indicators({}))
    assert "error" in out


def test_handle_submit_journal_ok() -> None:
    """submit_journal は検証 OK で {"ok": True}。"""
    out = _run(handlers.handle_submit_journal({"observations": "所見", "proposal": "提案"}))
    assert out == {"ok": True}


def test_handle_submit_journal_validation_error() -> None:
    """observations 欠落は {"error": ...}。"""
    out = _run(handlers.handle_submit_journal({"proposal": "x"}))
    assert "error" in out


def test_handle_submit_journal_tolerates_non_dict_change() -> None:
    """proposed_policy_change が文字列でも submission 全体を弾かず受理する（ADR-018・頑健性）。

    非力なモデルが変更案を markdown 文字列で渡しても、observations が揃っていれば {"ok": True}。
    壊れた変更案は落とす（nightly 側も非 dict は起票しない）。
    """
    out = _run(
        handlers.handle_submit_journal(
            {"observations": "所見", "proposed_policy_change": "- 方針を変える"}
        )
    )
    assert out == {"ok": True}


def test_tool_args_coerce_nullish_strings() -> None:
    """任意引数の "None"/"null"/"" は実 None に正規化される（ADR-018・頑健性）。

    非力なモデルが省略のつもりで文字列 "None" を渡しても int 検証で落ちないことを担保する。
    """
    from app.advisor.tools.schemas import GetPortfolioMetricsArgs, GetSignalsArgs

    assert GetPortfolioMetricsArgs.model_validate({"portfolio_id": "None"}).portfolio_id is None
    assert GetSignalsArgs.model_validate({"type": "null", "code": ""}).type is None
    assert GetSignalsArgs.model_validate({"type": "null", "code": ""}).code is None
    # 正常な値はそのまま通る（過剰な正規化をしない）。
    assert GetPortfolioMetricsArgs.model_validate({"portfolio_id": 3}).portfolio_id == 3


def test_coerce_policy_change_single_form_passes() -> None:
    """単一 {field, to} はそのまま正規化される（任意 from/reason も保持・ADR-013）。"""
    from app.advisor.tools.schemas import coerce_policy_change

    out = coerce_policy_change(
        {"field": "target_cash_ratio", "from": 0.25, "to": 0.4, "reason": "下落に備える"}
    )
    assert out == {"field": "target_cash_ratio", "to": 0.4, "from": 0.25, "reason": "下落に備える"}
    # to=0.0 は有効な目標値なので落とさない（is None 判定）。
    assert coerce_policy_change({"field": "target_cash_ratio", "to": 0.0}) == {
        "field": "target_cash_ratio",
        "to": 0.0,
    }


def test_coerce_policy_change_rejects_invalid_to_none_or_unknown() -> None:
    """多フィールド patch・非 dict・to 欠落・未知 field は None に倒す（U-10 裁定①・ADR-018）。"""
    from app.advisor.tools.schemas import coerce_policy_change

    # 多フィールド patch（field/to を欠く）→ required 検証で弾かれ None。
    assert coerce_policy_change({"max_position_weight": 0.2, "target_cash_ratio": 0.4}) is None
    assert coerce_policy_change("- markdown 文字列") is None  # 非 dict
    assert coerce_policy_change({"field": "target_cash_ratio", "to": None}) is None  # to=None
    assert coerce_policy_change({"field": "unknown_col", "to": 1}) is None  # enum 外 field
    assert coerce_policy_change(None) is None


def test_policy_field_enum_matches_default_policy() -> None:
    """PolicyField の enum は DEFAULT_POLICY のキーと一致する（ドリフトガード）。

    policy 列を増やして enum 更新を忘れたら CI で落とす（rationale は U-7 で提案外＝不一致でない）。
    """
    from typing import get_args

    from app.advisor.tools.schemas import PolicyField
    from app.services.policy import DEFAULT_POLICY

    assert set(get_args(PolicyField)) == set(DEFAULT_POLICY)


def test_submit_journal_schema_enforces_single_form() -> None:
    """submit_journal の JSON Schema が field enum ＋ required:[field,to] を持つ（LLM 契約）。"""
    from app.advisor.tools.schemas import SubmitJournalArgs

    defs = SubmitJournalArgs.model_json_schema()["$defs"]["ProposedPolicyChange"]
    assert defs["required"] == ["field", "to"]
    assert "enum" in defs["properties"]["field"]


def test_handle_submit_journal_drops_multi_field_change() -> None:
    """多フィールド patch の変更案は破棄して observations を受理する（U-10 裁定①・ADR-018）。

    弱モデルが複数列同時変更を渡しても submission 全体を弾かず {"ok": True}（変更案だけ落とす）。
    """
    out = _run(
        handlers.handle_submit_journal(
            {
                "observations": "所見",
                "proposed_policy_change": {
                    "max_position_weight": 0.2,
                    "target_cash_ratio": 0.4,
                },
            }
        )
    )
    assert out == {"ok": True}


def test_handle_get_financials_bridges(monkeypatch: pytest.MonkeyPatch) -> None:
    """handle_get_financials: repo.get_financials→{code, items}。"""

    class _FakeConn:
        def __enter__(self) -> _FakeConn:
            return self

        def __exit__(self, *_: Any) -> None:
            return None

    monkeypatch.setattr(handlers, "get_engine", lambda: type("E", (), {"connect": _FakeConn})())
    monkeypatch.setattr(
        handlers.repo,
        "get_financials",
        lambda conn, code: [{"disclosed_date": "2025-01-01", "eps": 12.3}],
    )
    out = _run(handlers.handle_get_financials({"code": "6758"}))
    assert out["code"] == "6758"
    assert out["items"][0]["eps"] == 12.3


# ---------------------------------------------------------------------------
# complete のコストガード（spec §7.1）
# ---------------------------------------------------------------------------


class _FakeUsage:
    prompt_tokens = 10
    completion_tokens = 20
    cost = 0.5


class _FakeMessage:
    content = "最終応答"
    tool_calls = None


class _FakeChoice:
    message = _FakeMessage()


class _FakeResp:
    choices = [_FakeChoice()]
    usage = _FakeUsage()


# complete は engine が解決した face を受け取る（ADR-058）。get_client を fake に差し替えるので
# base_url/api_key は実接続に使われない。
_FACE = ResolvedFace(
    face="chat", provider="openai", base_url="https://test.invalid/v1", api_key="k", model="m"
)


def _fake_client(create_fn: Any) -> Any:
    """get_client が返す AsyncOpenAI 互換の最小スタブ（chat.completions.create だけ持つ）。"""

    class _Completions:
        create = staticmethod(create_fn)

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    return _Client()


def _patch_fake_openai(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """llm.get_client を成功モック（chat.completions.create）に差し替える（ADR-058）。"""
    from app.advisor import llm

    calls: dict[str, Any] = {"created": 0}

    async def _fake_create(**_: Any) -> _FakeResp:
        calls["created"] += 1
        return _FakeResp()

    monkeypatch.setattr(llm, "get_client", lambda base_url, api_key: _fake_client(_fake_create))
    return calls


def test_complete_cost_guard_block(monkeypatch: pytest.MonkeyPatch, temp_db: None) -> None:
    """mode=block・当月累計が上限以上で CostGuardError（API を呼ばない）。"""
    from app.advisor import llm
    from app.config import settings

    monkeypatch.setattr(settings, "llm_cost_guard_mode", "block")
    monkeypatch.setattr(settings, "llm_cost_limit_usd", 50.0)
    monkeypatch.setattr(llm.repo, "sum_llm_cost_month", lambda conn, ym: 99.0)
    calls = _patch_fake_openai(monkeypatch)

    with pytest.raises(llm.CostGuardError):
        _run(llm.complete([{"role": "user", "content": "x"}], face=_FACE))
    assert calls["created"] == 0  # API は呼ばれない


def test_complete_cost_guard_warn_proceeds(monkeypatch: pytest.MonkeyPatch, temp_db: None) -> None:
    """mode=warn・超過でも呼び出しは進み usage が計上される。"""
    from app.advisor import llm
    from app.config import settings

    monkeypatch.setattr(settings, "llm_cost_guard_mode", "warn")
    monkeypatch.setattr(settings, "llm_cost_limit_usd", 0.0)  # 必ず超過
    monkeypatch.setattr(llm.repo, "sum_llm_cost_month", lambda conn, ym: 99.0)

    recorded: dict[str, Any] = {}

    def _fake_insert(conn: Any, **fields: Any) -> int:
        recorded.update(fields)
        return 1

    monkeypatch.setattr(llm.repo, "insert_llm_usage", _fake_insert)
    calls = _patch_fake_openai(monkeypatch)

    resp = _run(llm.complete([{"role": "user", "content": "x"}], face=_FACE, source="nightly"))
    assert resp.content == "最終応答"
    assert resp.tool_calls == []
    assert calls["created"] == 1  # 続行して API を呼んだ
    assert recorded["cost_usd"] == 0.5  # usage.cost が計上された
    assert recorded["source"] == "nightly"
    assert recorded["tokens_in"] == 10


def test_complete_parses_tool_calls(monkeypatch: pytest.MonkeyPatch, temp_db: None) -> None:
    """tool_calls あり応答を ToolCall 列にパース（arguments を json.loads）。"""
    from app.advisor import llm
    from app.config import settings

    monkeypatch.setattr(settings, "llm_cost_guard_mode", "off")

    class _FakeFn:
        name = "get_signals"
        arguments = '{"type": "momentum"}'

    class _FakeToolCall:
        id = "call_1"
        function = _FakeFn()

    class _Msg:
        content = None
        tool_calls = [_FakeToolCall()]

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]
        usage = None

    async def _fake_create(**_: Any) -> _Resp:
        return _Resp()

    monkeypatch.setattr(llm, "get_client", lambda base_url, api_key: _fake_client(_fake_create))

    resp = _run(llm.complete([{"role": "user", "content": "x"}], face=_FACE, tools=openai_tools(3)))
    assert resp.content is None
    assert len(resp.tool_calls) == 1
    tc = resp.tool_calls[0]
    assert tc.id == "call_1"
    assert tc.name == "get_signals"
    assert tc.arguments == {"type": "momentum"}
