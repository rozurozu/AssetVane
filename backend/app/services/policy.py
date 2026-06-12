"""policy 取得サービス — Phase 3 で policy テーブルが導入されるまでの橋渡し。

Phase 3 で `0006_advisor_state` マイグレーションが policy テーブルを作った後は、
自動的にそちらを読む。Phase 2 は DEFAULT_POLICY の既定値で動かす。
（phase2-spec.md §1「重要な設計判断」・ADR-013・ADR-015）

JSON 列（sector_caps/exclusions）の型変換も本モジュールが単一点で担う:
- 読み（DB の JSON 文字列 → dict/list）= `normalize_policy_row`
- 書き（dict/list → DB の JSON 文字列）= `encode_policy_field`
get_policy の戻り値は常に Python の型（sector_caps=dict・exclusions=list）であることを
契約とする。quant（compute_deviations/optimize_portfolio）等の下流はこの契約に依存する。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import Connection, inspect, text

# Phase 2 期間中は policy テーブルが存在しない。
# 将来 Phase 3 で `0006_advisor_state` が入ると自動的にそちらを読む。
# 値はすべて 0..1（比率）。比率系は spec §5 の単位約束に従う。
DEFAULT_POLICY: dict[str, Any] = {
    "risk_tolerance": "中",
    "time_horizon": "中",
    "target_cash_ratio": 0.25,
    "max_position_weight": 0.15,
    "sector_caps": {},
    "target_return": None,
    "no_leverage": 1,
    "exclusions": [],
}


def _as_dict(value: Any) -> dict[str, Any]:
    """JSON 文字列 or dict を dict にする（None・壊れた JSON・型不一致は {}）。"""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return dict(value) if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    """JSON 文字列 or list を list にする（None・壊れた JSON・型不一致は []）。"""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    return list(value) if isinstance(value, list) else []


def normalize_policy_row(row: dict[str, Any]) -> dict[str, Any]:
    """policy 行の JSON 列を Python の型へ正規化した新しい dict を返す。

    （ADR-013・phase3-spec.md §8.3）
    sector_caps は dict・exclusions は list に揃える（None・壊れた JSON・型不一致は
    既定値 {} / [] に倒す）。キーが存在する場合のみ変換するため、repo の生行
    （journal の policy_snapshot を dumps する前の正規化）にもそのまま使える。
    冪等で入力 dict は破壊せず、dict/list は新しいコピーを返す
    （DEFAULT_POLICY の可変既定値 {} / [] を呼び出し元と共有しない）。
    """
    normalized = dict(row)
    if "sector_caps" in normalized:
        normalized["sector_caps"] = _as_dict(normalized["sector_caps"])
    if "exclusions" in normalized:
        normalized["exclusions"] = _as_list(normalized["exclusions"])
    return normalized


def encode_policy_field(field: str, value: Any) -> Any:
    """policy 1 列の値を DB 形へ変換する（ADR-013・phase3-spec.md §8.3）。

    PUT /policy（routers/advisor_state.py）と提案承認（advisor/service.py の
    apply_policy_change）の両入口で共有する変換の単一点（入口間ドリフトを断つ）。
    - sector_caps/exclusions: dict/list → json.dumps。JSON 文字列で来たらパース検証して
      再 dumps（二重エンコード防止）。None は「未設定」として既定値（{} / []）を格納。
      型不一致は ValueError（router 境界が 409 に翻訳し、LLM 由来の壊れ値を DB に書かない）。
    - no_leverage: truthy → 1 / falsy → 0。
    - その他のスカラ列: 素通し。
    """
    if field in ("sector_caps", "exclusions"):
        expected: type = dict if field == "sector_caps" else list
        if value is None:
            value = expected()
        elif isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError as exc:
                raise ValueError(
                    f"policy.{field} は {expected.__name__} が必要です"
                    f"（JSON として解釈できない文字列: {value!r}）"
                ) from exc
        if not isinstance(value, expected):
            raise ValueError(f"policy.{field} は {expected.__name__} が必要です: {value!r}")
        return json.dumps(value, ensure_ascii=False)
    if field == "no_leverage":
        return 1 if value else 0
    return value


def get_policy(conn: Connection) -> dict[str, Any]:
    """policy を返す。policy テーブルが存在すれば先頭行を読み、なければ DEFAULT_POLICY を返す。

    Phase 3 で `0006_advisor_state` マイグレーションが policy テーブルを作ると、
    自動的にそちらを読む設計（phase2-spec.md §1「重要な設計判断」）。
    テーブル存在チェックは `sqlalchemy.inspect` で行う（ADR-005 DB は FastAPI のみ）。

    戻り値の sector_caps/exclusions は常に dict/list（normalize_policy_row で正規化）。
    DB 行の JSON 文字列をそのまま返さない（文字列のまま下流の quant に流れると
    `.items()` 等で落ちる＝2026-06-12 の GET /asset-overview 500 の再発防止）。
    """
    insp = inspect(conn)
    if not insp.has_table("policy"):
        return normalize_policy_row(DEFAULT_POLICY)

    # policy テーブルが存在する場合は先頭行を読む（Phase 3 以降）
    row = conn.execute(text("SELECT * FROM policy LIMIT 1")).mappings().first()
    if row is None:
        return normalize_policy_row(DEFAULT_POLICY)

    policy = dict(DEFAULT_POLICY)
    policy.update(dict(row))
    return normalize_policy_row(policy)
