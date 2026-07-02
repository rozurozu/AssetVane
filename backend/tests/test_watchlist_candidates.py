"""チャットからのウォッチリスト追加＝propose_watchlist の検証（ADR-080）。

LLM はモック・DB は temp_db/一時 SQLite・ネット非依存。検証対象:
- build_watchlist_candidates_from_tool_runs（読み取り専用・**永続しない**）が code→社名を stocks で
  解決／未知・US は drop（ADR-018）／重複は初出のみ／reason 保持／不正な 1 件だけ skip。
- handle_propose_watchlist（検証 only）が候補を解決して {ok, resolved, dropped} を返す・
  空 candidates は ok・未知は dropped・例外は {error}（ADR-014/018）。
- /chat：propose_watchlist を呼ぶと応答 watchlist_candidates に解決済み候補が載る（surfacing は
  昼 router だけ・ADR-080）。通常ターンは空。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.advisor import journaling, service
from app.advisor.llm import LLMResponse, ToolCall
from app.advisor.tools import handlers
from app.db import repo
from app.db.engine import get_engine


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _seed_stocks() -> None:
    """JP 2 銘柄をマスタに焼く（code→社名解決がヒットするように）。"""
    repo.upsert_stocks(
        [
            {"code": "37120", "company_name": "情報企画"},
            {"code": "61380", "company_name": "ダイジェット工業"},
        ]
    )


def _wl_run(candidates: list[dict[str, str]]) -> dict[str, Any]:
    return {"name": "propose_watchlist", "args": {"candidates": candidates}}


# --- build_watchlist_candidates_from_tool_runs（読み取り専用ブリッジ・永続しない）--------


def test_build_resolves_known_codes(temp_db: None) -> None:
    """既知 code は社名付きで解決し reason を保持する（追加時に note へ焼く元・ADR-080）。"""
    _seed_stocks()
    runs = [
        _wl_run(
            [
                {"code": "37120", "reason": "低PER×ネットキャッシュ比>1"},
                {"code": "61380", "reason": "次点・小型値ごろ"},
            ]
        )
    ]
    with get_engine().connect() as conn:
        out = journaling.build_watchlist_candidates_from_tool_runs(conn, tool_runs=runs)
    assert out == [
        {"code": "37120", "company_name": "情報企画", "reason": "低PER×ネットキャッシュ比>1"},
        {"code": "61380", "company_name": "ダイジェット工業", "reason": "次点・小型値ごろ"},
    ]


def test_build_drops_unknown_code(temp_db: None) -> None:
    """未知コード（幻覚/US）は候補から drop（queue に幻覚を入れない・ADR-018）。"""
    _seed_stocks()
    runs = [_wl_run([{"code": "00000", "reason": "幻覚"}, {"code": "37120", "reason": "実在"}])]
    with get_engine().connect() as conn:
        out = journaling.build_watchlist_candidates_from_tool_runs(conn, tool_runs=runs)
    assert [c["code"] for c in out] == ["37120"]


def test_build_dedups_same_code(temp_db: None) -> None:
    """同一 code は初出のみ（重複提示を 1 件に畳む）。"""
    _seed_stocks()
    runs = [_wl_run([{"code": "37120", "reason": "1回目"}, {"code": "37120", "reason": "2回目"}])]
    with get_engine().connect() as conn:
        out = journaling.build_watchlist_candidates_from_tool_runs(conn, tool_runs=runs)
    assert len(out) == 1
    assert out[0]["reason"] == "1回目"


def test_build_reason_optional(temp_db: None) -> None:
    """reason 欠落でも候補は落とさず reason='' で残す（銘柄を取りこぼさない・ADR-080）。"""
    _seed_stocks()
    runs = [{"name": "propose_watchlist", "args": {"candidates": [{"code": "37120"}]}}]
    with get_engine().connect() as conn:
        out = journaling.build_watchlist_candidates_from_tool_runs(conn, tool_runs=runs)
    assert out == [{"code": "37120", "company_name": "情報企画", "reason": ""}]


def test_build_skips_malformed_candidate(temp_db: None) -> None:
    """1 件が不正（code 欠落）でも有効分は残す（per-item グレースフル・ADR-018）。"""
    _seed_stocks()
    runs = [
        {
            "name": "propose_watchlist",
            "args": {"candidates": [{"reason": "code なし"}, {"code": "61380", "reason": "ok"}]},
        }
    ]
    with get_engine().connect() as conn:
        out = journaling.build_watchlist_candidates_from_tool_runs(conn, tool_runs=runs)
    assert [c["code"] for c in out] == ["61380"]


def test_build_empty_when_no_tool(temp_db: None) -> None:
    """propose_watchlist が無いターンは空（副作用ゼロ）。"""
    with get_engine().connect() as conn:
        out = journaling.build_watchlist_candidates_from_tool_runs(
            conn, tool_runs=[{"name": "get_signals", "args": {}}]
        )
    assert out == []


# --- handle_propose_watchlist（検証 only・read-only）----------------------------------


def test_handle_propose_watchlist_resolves(temp_db: None) -> None:
    """候補を解決して resolved/dropped を返す（未知は dropped・LLM が言及できる）。"""
    _seed_stocks()
    out = _run(
        handlers.handle_propose_watchlist(
            {"candidates": [{"code": "37120", "reason": "x"}, {"code": "00000", "reason": "y"}]}
        )
    )
    assert out["ok"] is True
    assert [c["code"] for c in out["resolved"]] == ["37120"]
    assert out["resolved"][0]["company_name"] == "情報企画"
    assert out["dropped"] == ["00000"]


def test_handle_propose_watchlist_empty_ok(temp_db: None) -> None:
    """空 candidates は ok（無理に候補を出さなくてよい）。"""
    _seed_stocks()
    out = _run(handlers.handle_propose_watchlist({"candidates": []}))
    assert out == {"ok": True, "resolved": [], "dropped": []}


def test_handle_propose_watchlist_db_error_returns_error(
    temp_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DB アクセスが例外でも {"error"} を返し例外を漏らさない（dispatch を止めない・ADR-018）。"""
    _seed_stocks()

    def _boom(conn: Any, code: str) -> dict[str, Any] | None:
        raise RuntimeError("DB が壊れた")

    monkeypatch.setattr(handlers.repo, "get_stock", _boom)
    out = _run(
        handlers.handle_propose_watchlist({"candidates": [{"code": "37120", "reason": "x"}]})
    )
    assert "error" in out
    assert "ok" not in out


# --- /chat：propose_watchlist が候補を surfacing する（surfacing は昼 router だけ・ADR-080）--


def _mock_complete(monkeypatch: pytest.MonkeyPatch, responses: list[LLMResponse]) -> None:
    async def _fake_complete(messages: Any, **_: Any) -> LLMResponse:
        return responses.pop(0)

    monkeypatch.setattr(service, "complete", _fake_complete)


def test_chat_propose_watchlist_surfaces_candidates(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """propose_watchlist を呼ぶと /chat 応答 watchlist_candidates に解決済み候補が載る（ADR-080）。

    追加はユーザーが UI で行う契約（AI は watchlist を書かない）。未知 code は drop される。
    """
    repo.upsert_stocks(
        [
            {"code": "37120", "company_name": "情報企画"},
            {"code": "61380", "company_name": "ダイジェット工業"},
        ]
    )
    _mock_complete(
        monkeypatch,
        [
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="w1",
                        name="propose_watchlist",
                        arguments={
                            "candidates": [
                                {"code": "37120", "reason": "低PER×NC>1"},
                                {"code": "00000", "reason": "幻覚"},
                            ]
                        },
                    )
                ],
            ),
            LLMResponse(content="ウォッチ候補を提示したのだ", tool_calls=[]),
        ],
    )

    res = client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "清原式で候補を絞って"}]},
    )
    assert res.status_code == 200
    cands = res.json()["watchlist_candidates"]
    assert [c["code"] for c in cands] == ["37120"]  # 未知 00000 は drop
    assert cands[0]["company_name"] == "情報企画"
    assert cands[0]["reason"] == "低PER×NC>1"


def test_chat_no_watchlist_returns_empty(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """propose_watchlist を呼ばない通常ターンでは watchlist_candidates は空。"""
    _mock_complete(monkeypatch, [LLMResponse(content="ふつうの応答なのだ", tool_calls=[])])
    res = client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "こんにちは"}]},
    )
    assert res.status_code == 200
    assert res.json()["watchlist_candidates"] == []
