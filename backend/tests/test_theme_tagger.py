"""テーマタグの grounded タガー（theme_tagger）を検証する。

担保すること（ADR-050 改訂・段階A）:
- 既存語彙 exact 再用: vocabulary 内の名前は themes 目録に二重追加されない（n_new_themes=0）。
- 新テーマ提案: 目録に無い名前は insert_themes_if_absent で themes に増える。
- grounding 検証: evidence が本文（空白正規化後）に無いタグは破棄され書き込まれない。
- 堅牢化: 壊れた JSON 応答 → タグを付けない側に倒す（stock_themes に書かれない・ADR-018）。
- タグ爆発防止: 6 件以上返されても _MAX_THEMES_PER_STOCK=5 件で打ち切る。
- skip: 事業説明テキストが無い銘柄は LLM を呼ばず {"skipped": True} で静かに返す。

LLM（engine.generate_once）は必ずモック（ネットを叩かない＝testing-strategy）。dossier 同様に
theme_tagger も遅延 import で engine.generate_once を呼ぶため、モックは engine モジュール側に
当てる。DB は temp_db の一時 SQLite で themes / stock_themes への実書き込みまで検証する。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.advisor import engine, theme_tagger
from app.db import repo
from app.db.engine import get_engine

# 実在テキスト（英語・longBusinessSummary 想定＝ADR-055）。evidence はここからの逐語引用になる。
_DESCRIPTION = (
    "NVIDIA Corporation provides graphics and compute solutions.\n"
    "The company offers AI computing platforms for data centers,\n"
    "and provides cybersecurity acceleration software for enterprises."
)


def _seed_description(market: str = "US", code: str = "NVDA") -> None:
    """company_descriptions に実在テキストを 1 行仕込む（タガーの信号源・ADR-050/056）。"""
    repo.upsert_company_description(
        {
            "market": market,
            "code": code,
            "source": "yfinance",
            "description_text": _DESCRIPTION,
        }
    )


def _stub_generate(
    monkeypatch: pytest.MonkeyPatch,
    response: str,
    *,
    capture: dict[str, Any] | None = None,
) -> None:
    """engine.generate_once を固定文字列を返すスタブに差し替える（messages/source を capture）。"""

    async def _fake_generate(messages, *, source="chat"):  # noqa: ANN001
        if capture is not None:
            capture["called"] = True
            capture["messages"] = messages
            capture["source"] = source
        return response

    monkeypatch.setattr(engine, "generate_once", _fake_generate)


def _tag(market: str = "US", code: str = "NVDA") -> dict[str, Any]:
    """読み取り接続を貸して tag_stock_themes を回す（書き込みは repo の W1 関数が自前 begin）。"""
    with get_engine().connect() as conn:
        return asyncio.run(theme_tagger.tag_stock_themes(conn, market=market, code=code))


def _themes_response(items: list[dict[str, str]]) -> str:
    """タガー応答の JSON 文字列を組み立てる。"""
    return json.dumps({"themes": items}, ensure_ascii=False)


def _stock_theme_names(market: str = "US", code: str = "NVDA") -> list[str]:
    """stock_themes に実際に書かれたテーマ名（昇順）を読む。"""
    with get_engine().connect() as conn:
        return [r["theme_name"] for r in repo.get_stock_themes(conn, market, code)]


def test_exact_reuse_does_not_grow_vocabulary(
    monkeypatch: pytest.MonkeyPatch, temp_db: None
) -> None:
    """既存語彙 exact 再用: vocabulary 内の名前は themes に増えず n_new_themes=0。"""
    _seed_description()
    repo.insert_themes_if_absent(["AI需要", "半導体"], "2026-06-10T00:00:00+00:00")
    _stub_generate(
        monkeypatch,
        _themes_response(
            [{"name": "AI需要", "evidence": "AI computing platforms for data centers"}]
        ),
    )

    result = _tag()

    assert result == {"code": "NVDA", "themes": ["AI需要"], "n_new_themes": 0}
    with get_engine().connect() as conn:
        assert repo.list_theme_names(conn) == ["AI需要", "半導体"]  # 目録は増えない
    assert _stock_theme_names() == ["AI需要"]


def test_new_theme_added_to_vocabulary(monkeypatch: pytest.MonkeyPatch, temp_db: None) -> None:
    """新テーマ提案: 目録に無い名前は insert_themes_if_absent で themes に増える。"""
    _seed_description()
    repo.insert_themes_if_absent(["半導体"], "2026-06-10T00:00:00+00:00")
    _stub_generate(
        monkeypatch,
        _themes_response(
            [
                {"name": "半導体", "evidence": "graphics and compute solutions"},
                {
                    "name": "サイバーセキュリティ",
                    "evidence": "cybersecurity acceleration software",
                },
            ]
        ),
    )

    result = _tag()

    assert result["themes"] == ["半導体", "サイバーセキュリティ"]
    assert result["n_new_themes"] == 1
    with get_engine().connect() as conn:
        assert repo.list_theme_names(conn) == ["サイバーセキュリティ", "半導体"]
    assert _stock_theme_names() == ["サイバーセキュリティ", "半導体"]


def test_ungrounded_evidence_is_discarded(monkeypatch: pytest.MonkeyPatch, temp_db: None) -> None:
    """grounding 検証: evidence が本文に無いタグは破棄され、根拠あるタグだけ書かれる。"""
    _seed_description()
    _stub_generate(
        monkeypatch,
        _themes_response(
            [
                # 改行をまたぐ引用も空白正規化で本文一致とみなす（検証仕様）。
                {
                    "name": "データセンター",
                    "evidence": "AI computing platforms for data centers, and provides",
                },
                {"name": "宇宙", "evidence": "satellite communication systems"},  # 本文に無い
                {"name": "防衛", "evidence": ""},  # 空 evidence も破棄
            ]
        ),
    )

    result = _tag()

    assert result["themes"] == ["データセンター"]
    assert _stock_theme_names() == ["データセンター"]


def test_broken_json_writes_nothing(monkeypatch: pytest.MonkeyPatch, temp_db: None) -> None:
    """壊れた JSON 応答はタグを付けない側に倒す（stock_themes/themes に書かれない・ADR-018）。"""
    _seed_description()
    _stub_generate(monkeypatch, "これは JSON ではない応答")

    result = _tag()

    assert result == {"code": "NVDA", "themes": [], "n_new_themes": 0}
    assert _stock_theme_names() == []
    with get_engine().connect() as conn:
        assert repo.list_theme_names(conn) == []


def test_more_than_five_themes_truncated(monkeypatch: pytest.MonkeyPatch, temp_db: None) -> None:
    """6 件以上返されても _MAX_THEMES_PER_STOCK=5 件で打ち切る（タグ爆発防止・ADR-050）。"""
    _seed_description()
    items = [
        {"name": f"テーマ{i}", "evidence": "graphics and compute solutions"} for i in range(1, 7)
    ]
    _stub_generate(monkeypatch, _themes_response(items))

    result = _tag()

    assert result["themes"] == ["テーマ1", "テーマ2", "テーマ3", "テーマ4", "テーマ5"]
    assert len(_stock_theme_names()) == 5


def test_missing_description_skips_quietly(monkeypatch: pytest.MonkeyPatch, temp_db: None) -> None:
    """事業説明テキストが無い銘柄は LLM を呼ばず skip する（根拠なければタグ付けない）。"""
    capture: dict[str, Any] = {}
    _stub_generate(monkeypatch, _themes_response([]), capture=capture)

    result = _tag(code="ZZZZ")

    assert result == {"code": "ZZZZ", "themes": [], "skipped": True}
    assert "called" not in capture  # LLM は呼ばれない
    assert _stock_theme_names(code="ZZZZ") == []


def test_prompt_carries_symbol_vocabulary_and_source(
    monkeypatch: pytest.MonkeyPatch, temp_db: None
) -> None:
    """LLM へは symbol（同一性）・本文・語彙が JSON で渡り source="tagger" で呼ばれる。"""
    _seed_description()
    repo.insert_themes_if_absent(["半導体"], "2026-06-10T00:00:00+00:00")
    capture: dict[str, Any] = {}
    _stub_generate(monkeypatch, _themes_response([]), capture=capture)

    _tag()

    assert capture["source"] == "tagger"
    user_payload = json.loads(capture["messages"][1]["content"])
    assert user_payload == {
        "symbol": "NVDA",
        "description": _DESCRIPTION,
        "vocabulary": ["半導体"],
    }


def test_code_fenced_json_is_parsed(monkeypatch: pytest.MonkeyPatch, temp_db: None) -> None:
    """```json フェンスで包まれた応答も中身をパースしてタグ付けできる（安いモデルの癖への防御）。"""
    _seed_description()
    fenced = (
        "```json\n"
        + _themes_response(
            [{"name": "AI需要", "evidence": "AI computing platforms for data centers"}]
        )
        + "\n```"
    )
    _stub_generate(monkeypatch, fenced)

    result = _tag()

    assert result["themes"] == ["AI需要"]
    assert _stock_theme_names() == ["AI需要"]
