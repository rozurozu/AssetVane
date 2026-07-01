"""card_triage の応答パース堅牢化を検証（ADR-062・ADR-018）。

担保: _parse_assist_response が正常 JSON / コードフェンス付きを AssistResult に正規化し、壊れ応答・
verdict 値域外・空 content は None（draft のまま）に倒す。空文字の quant_note/linked_signal_type は
None に畳み、level は値域外を None に倒す。LLM は呼ばない純パース関数のテスト
（旧 _parse_triage_response は assist に統合され撤去・#15）。
"""

from __future__ import annotations

from app.advisor.card_triage import _parse_assist_response


def test_parse_valid_active() -> None:
    """active＋linked_signal_type の正常 JSON を AssistResult に正規化する。"""
    content = (
        '{"title": "見出し", "when_to_apply": "この状況", "level": "market", '
        '"verdict": "active", "reason": "既存 momentum の読み方", '
        '"quant_note": null, "linked_signal_type": "momentum"}'
    )
    result = _parse_assist_response(content)
    assert result is not None
    assert result.title == "見出し"
    assert result.when_to_apply == "この状況"
    assert result.level == "market"
    assert result.verdict == "active"
    assert result.linked_signal_type == "momentum"
    assert result.quant_note is None
    assert result.reason == "既存 momentum の読み方"


def test_parse_needs_quant_with_note() -> None:
    """needs_quant＋quant_note を取り出す（verdict は大小無視で正規化）。"""
    content = (
        '{"title": "t", "verdict": "NEEDS_QUANT", "reason": "新指標が要る", '
        '"quant_note": "X を計算する"}'
    )
    result = _parse_assist_response(content)
    assert result is not None
    assert result.verdict == "needs_quant"
    assert result.quant_note == "X を計算する"


def test_parse_code_fence_stripped() -> None:
    """```json ... ``` で包まれた応答も中身だけパースする。"""
    content = '```json\n{"title": "t", "verdict": "rejected", "reason": "一般常識"}\n```'
    result = _parse_assist_response(content)
    assert result is not None
    assert result.verdict == "rejected"


def test_parse_empty_strings_folded_to_none() -> None:
    """空文字の quant_note/linked_signal_type/title は None・""（空 title 許容）に畳む。"""
    content = (
        '{"title": "", "verdict": "active", "reason": "", '
        '"quant_note": "", "linked_signal_type": ""}'
    )
    result = _parse_assist_response(content)
    assert result is not None
    assert result.title == ""  # 空 title は許容（呼び出し側が扱う）
    assert result.quant_note is None
    assert result.linked_signal_type is None
    assert result.reason == ""


def test_parse_out_of_range_level_folded_to_none() -> None:
    """level が値域外なら None に倒す。"""
    content = '{"title": "t", "level": "banana", "verdict": "active", "reason": "x"}'
    result = _parse_assist_response(content)
    assert result is not None
    assert result.level is None


def test_parse_invalid_json_returns_none() -> None:
    """JSON でない応答は None（draft のまま・ADR-018）。"""
    assert _parse_assist_response("これは JSON ではない") is None


def test_parse_bad_verdict_returns_none() -> None:
    """値域外の verdict は None。"""
    assert _parse_assist_response('{"title": "t", "verdict": "banana", "reason": "x"}') is None


def test_parse_empty_content_returns_none() -> None:
    """空 content / None は None。"""
    assert _parse_assist_response("") is None
    assert _parse_assist_response(None) is None
