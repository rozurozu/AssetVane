"""card_triage の応答パース堅牢化を検証（ADR-062・ADR-018）。

担保: _parse_triage_response が正常 JSON / コードフェンス付きを TriageResult に正規化し、壊れ応答・
verdict 値域外・空 content は None（draft のまま）に倒す。空文字の quant_note/linked_signal_type は
None に畳む。LLM は呼ばない純パース関数のテスト。
"""

from __future__ import annotations

from app.advisor.card_triage import _parse_triage_response


def test_parse_valid_active() -> None:
    """active＋linked_signal_type の正常 JSON を TriageResult に正規化する。"""
    content = (
        '{"verdict": "active", "reason": "既存 momentum の読み方", '
        '"quant_note": null, "linked_signal_type": "momentum"}'
    )
    result = _parse_triage_response(content)
    assert result is not None
    assert result.verdict == "active"
    assert result.linked_signal_type == "momentum"
    assert result.quant_note is None
    assert result.reason == "既存 momentum の読み方"


def test_parse_needs_quant_with_note() -> None:
    """needs_quant＋quant_note を取り出す。"""
    content = '{"verdict": "NEEDS_QUANT", "reason": "新指標が要る", "quant_note": "X を計算する"}'
    result = _parse_triage_response(content)
    assert result is not None
    assert result.verdict == "needs_quant"  # 大小無視で正規化
    assert result.quant_note == "X を計算する"


def test_parse_code_fence_stripped() -> None:
    """```json ... ``` で包まれた応答も中身だけパースする。"""
    content = '```json\n{"verdict": "rejected", "reason": "一般常識"}\n```'
    result = _parse_triage_response(content)
    assert result is not None
    assert result.verdict == "rejected"


def test_parse_empty_strings_folded_to_none() -> None:
    """空文字の quant_note/linked_signal_type は None に畳む。"""
    content = '{"verdict": "active", "reason": "", "quant_note": "", "linked_signal_type": ""}'
    result = _parse_triage_response(content)
    assert result is not None
    assert result.quant_note is None
    assert result.linked_signal_type is None
    assert result.reason == ""


def test_parse_invalid_json_returns_none() -> None:
    """JSON でない応答は None（draft のまま・ADR-018）。"""
    assert _parse_triage_response("これは JSON ではない") is None


def test_parse_bad_verdict_returns_none() -> None:
    """値域外の verdict は None。"""
    assert _parse_triage_response('{"verdict": "banana", "reason": "x"}') is None


def test_parse_empty_content_returns_none() -> None:
    """空 content / None は None。"""
    assert _parse_triage_response("") is None
    assert _parse_triage_response(None) is None
