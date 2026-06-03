"""policy_compiler のユニットテスト。

DB 非依存・LLM 非依存の純粋関数テスト。
（spec §10・ADR-013/014）
"""

from __future__ import annotations

import json

import pytest

from app.advisor.policy_compiler import compile_policy

# ---------------------------------------------------------------------------
# policy=None → 未設定メッセージ
# ---------------------------------------------------------------------------


def test_compile_policy_none_returns_not_set_message() -> None:
    """policy=None のとき「方針はまだ設定されていない」旨の文を返す。"""
    result = compile_policy(None)
    assert "まだ設定されていない" in result
    assert "対話" in result


# ---------------------------------------------------------------------------
# 全部入りの policy dict
# ---------------------------------------------------------------------------


FULL_POLICY: dict[str, object] = {
    "risk_tolerance": "高",
    "time_horizon": "短〜中",
    "target_cash_ratio": 0.10,
    "max_position_weight": 0.20,
    "sector_caps": {"0050": 0.30, "0070": 0.15},
    "target_return": 0.15,
    "no_leverage": 1,
    "exclusions": ["7203", "9432"],
    "rationale": "攻めるが退場はしない",
}


def test_compile_policy_full_risk_tolerance() -> None:
    """risk_tolerance が文に含まれる。"""
    result = compile_policy(FULL_POLICY)
    assert "リスク許容度は高" in result


def test_compile_policy_full_time_horizon() -> None:
    """time_horizon が文に含まれる。"""
    result = compile_policy(FULL_POLICY)
    assert "短〜中" in result


def test_compile_policy_full_cash_ratio_percent() -> None:
    """target_cash_ratio が ×100 して % で表示される（0.10 → 10%）。"""
    result = compile_policy(FULL_POLICY)
    assert "10%" in result or "10 %" in result


def test_compile_policy_full_max_position_percent() -> None:
    """max_position_weight が ×100 して % で表示される（0.20 → 20%）。"""
    result = compile_policy(FULL_POLICY)
    assert "20%" in result or "20 %" in result


def test_compile_policy_full_target_return_percent() -> None:
    """target_return が ×100 して % で表示される（0.15 → 15%）。"""
    result = compile_policy(FULL_POLICY)
    assert "15%" in result or "15 %" in result


def test_compile_policy_full_no_leverage_wording() -> None:
    """no_leverage=1 のとき「信用・レバレッジは使わない」文言が含まれる。"""
    result = compile_policy(FULL_POLICY)
    assert "信用" in result
    assert "レバレッジ" in result
    assert "ゼロカット" in result


def test_compile_policy_full_exclusions() -> None:
    """exclusions が「次は除外」として文に含まれる。"""
    result = compile_policy(FULL_POLICY)
    assert "除外" in result
    assert "7203" in result
    assert "9432" in result


def test_compile_policy_full_rationale() -> None:
    """rationale（理念）が末尾に含まれる。"""
    result = compile_policy(FULL_POLICY)
    assert "攻めるが退場はしない" in result


# ---------------------------------------------------------------------------
# JSON 文字列で格納された sector_caps / exclusions も受けられる
# ---------------------------------------------------------------------------


def test_compile_policy_sector_caps_as_json_string() -> None:
    """sector_caps が JSON 文字列でも正しく解析される。"""
    policy = {
        "sector_caps": json.dumps({"0050": 0.25}),
    }
    result = compile_policy(policy)
    assert "25%" in result or "25 %" in result


def test_compile_policy_exclusions_as_json_string() -> None:
    """exclusions が JSON 文字列でも正しく解析される。"""
    policy = {
        "exclusions": json.dumps(["6758"]),
    }
    result = compile_policy(policy)
    assert "除外" in result
    assert "6758" in result


# ---------------------------------------------------------------------------
# 部分欠損（None/空）でも壊れない
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "policy",
    [
        {},
        {"risk_tolerance": None},
        {"target_cash_ratio": None, "max_position_weight": None},
        {"no_leverage": 0},
        {"exclusions": []},
        {"exclusions": None},
        {"sector_caps": {}},
        {"sector_caps": None},
    ],
)
def test_compile_policy_partial_does_not_raise(policy: dict[str, object]) -> None:
    """部分欠損の policy dict でも例外を投げない。"""
    result = compile_policy(policy)
    assert isinstance(result, str)
    assert len(result) > 0


def test_compile_policy_no_leverage_false_not_in_result() -> None:
    """no_leverage=0 のときレバレッジ禁止文は出ない。"""
    policy: dict[str, object] = {"no_leverage": 0}
    result = compile_policy(policy)
    assert "レバレッジ" not in result


def test_compile_policy_only_risk_tolerance() -> None:
    """risk_tolerance だけ設定した場合でも他の欠損フィールドは無視される。"""
    policy: dict[str, object] = {"risk_tolerance": "中"}
    result = compile_policy(policy)
    assert "リスク許容度は中" in result
    assert "現金バッファ" not in result
    assert "除外" not in result
