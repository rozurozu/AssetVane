"""policy サービスのテスト（services/policy.py・ADR-013・phase3-spec.md §8.3）。

DB は temp_db（一時 SQLite）。検証対象:
- get_policy: policy 行が存在しても sector_caps/exclusions が dict/list で返ること
  （DB の JSON 文字列をパースせず返し、truthy な文字列 '{}' が compute_deviations の
  `.items()` で落ちた 2026-06-12 の GET /asset-overview 500 の回帰テスト）。
- normalize_policy_row: 冪等・入力非破壊・DEFAULT_POLICY の可変既定値を共有しない。
- encode_policy_field: dict/list→JSON 文字列・no_leverage→0/1・二重エンコード防止・
  型不一致は ValueError（router 境界が 409 に翻訳）。
"""

from __future__ import annotations

import json

import pytest

from app.db import repo
from app.db.engine import get_engine
from app.services.policy import (
    DEFAULT_POLICY,
    encode_policy_field,
    get_policy,
    normalize_policy_row,
)

# ---------------------------------------------------------------------------
# get_policy
# ---------------------------------------------------------------------------


def test_get_policy_no_row_returns_default_types(temp_db: None) -> None:
    """policy 行が無ければ DEFAULT_POLICY 相当が dict/list の型で返る（ADR-013）。"""
    with get_engine().connect() as conn:
        policy = get_policy(conn)
    assert policy["sector_caps"] == {}
    assert policy["exclusions"] == []
    assert policy["target_cash_ratio"] == 0.25


def test_get_policy_parses_json_columns(temp_db: None) -> None:
    """policy 行が存在しても sector_caps/exclusions は dict/list で返る（500 回帰・ADR-013）。

    実機で観測した形（sector_caps='{}'）と非空 JSON の両方を確認する。文字列 '{}' は
    truthy なので `or {}` をすり抜け、下流 quant の `.items()` で AttributeError になっていた。
    """
    # 実機で 500 を引き起こした形（空の JSON オブジェクト/配列の文字列）。
    with get_engine().begin() as conn:
        repo.upsert_policy(conn, {"sector_caps": "{}", "exclusions": "[]"})
    with get_engine().connect() as conn:
        policy = get_policy(conn)
    assert policy["sector_caps"] == {}
    assert policy["exclusions"] == []

    # 非空 JSON も型に直って返る。
    with get_engine().begin() as conn:
        repo.upsert_policy(
            conn,
            {
                "sector_caps": json.dumps({"3700": 0.3}),
                "exclusions": json.dumps(["72030"]),
            },
        )
    with get_engine().connect() as conn:
        policy = get_policy(conn)
    assert policy["sector_caps"] == {"3700": 0.3}
    assert policy["exclusions"] == ["72030"]


def test_get_policy_broken_json_falls_back_default(temp_db: None) -> None:
    """壊れた JSON・型不一致の列値は既定値（{} / []）に倒れ、落とさない（ADR-013）。"""
    with get_engine().begin() as conn:
        repo.upsert_policy(conn, {"sector_caps": "{broken", "exclusions": '{"a": 1}'})
    with get_engine().connect() as conn:
        policy = get_policy(conn)
    assert policy["sector_caps"] == {}
    assert policy["exclusions"] == []


# ---------------------------------------------------------------------------
# normalize_policy_row
# ---------------------------------------------------------------------------


def test_normalize_policy_row_idempotent_and_non_destructive() -> None:
    """正規化は冪等・入力非破壊・キーが無ければ追加しない（repo 生行にも使える契約）。"""
    raw = {"sector_caps": '{"3700": 0.3}', "exclusions": '["72030"]', "rationale": "r"}
    once = normalize_policy_row(raw)
    twice = normalize_policy_row(once)
    assert once == twice
    assert once["sector_caps"] == {"3700": 0.3}
    assert once["exclusions"] == ["72030"]
    # 入力は破壊されない（raw は文字列のまま）。
    assert raw["sector_caps"] == '{"3700": 0.3}'
    # キーが無い行には追加しない。
    assert "sector_caps" not in normalize_policy_row({"rationale": "r"})


def test_normalize_policy_row_does_not_share_default_mutables() -> None:
    """DEFAULT_POLICY の可変既定値（{} / []）を呼び出し元と共有しない（aliasing 防止）。"""
    normalized = normalize_policy_row(DEFAULT_POLICY)
    normalized["sector_caps"]["3700"] = 1.0
    normalized["exclusions"].append("72030")
    assert DEFAULT_POLICY["sector_caps"] == {}
    assert DEFAULT_POLICY["exclusions"] == []


# ---------------------------------------------------------------------------
# encode_policy_field
# ---------------------------------------------------------------------------


def test_encode_policy_field_converts_to_db_form() -> None:
    """dict/list は JSON 文字列・no_leverage は 0/1・その他は素通し（ADR-013）。"""
    assert json.loads(encode_policy_field("sector_caps", {"3700": 0.3})) == {"3700": 0.3}
    assert json.loads(encode_policy_field("exclusions", ["72030"])) == ["72030"]
    assert encode_policy_field("no_leverage", True) == 1
    assert encode_policy_field("no_leverage", False) == 0
    assert encode_policy_field("target_cash_ratio", 0.25) == 0.25
    # None は「未設定」として既定値を格納する。
    assert encode_policy_field("sector_caps", None) == "{}"
    assert encode_policy_field("exclusions", None) == "[]"


def test_encode_policy_field_json_string_not_double_encoded() -> None:
    """JSON 文字列で渡されてもパース検証して再 dumps する（二重エンコードしない）。"""
    encoded = encode_policy_field("sector_caps", '{"3700": 0.3}')
    assert json.loads(encoded) == {"3700": 0.3}


def test_encode_policy_field_type_mismatch_raises() -> None:
    """型不一致（LLM 由来の壊れ値）は ValueError（router 境界が 409 に翻訳・ADR-013）。"""
    with pytest.raises(ValueError):
        encode_policy_field("sector_caps", [1, 2])
    with pytest.raises(ValueError):
        encode_policy_field("sector_caps", "not json")
    with pytest.raises(ValueError):
        encode_policy_field("exclusions", {"a": 1})
