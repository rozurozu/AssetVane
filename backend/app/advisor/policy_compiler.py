"""policy テーブルの 1 行 dict を POLICY 層の自然文へ整形するコンパイラ。

（ADR-013・ADR-014・spec §3.2）

DB の `policy` 列はすべて 0..1 の比率（`target_cash_ratio` 等）を保持するが、
LLM に生の列名・数値をそのまま渡す「生データ丸投げ」は禁止（ADR-014）。
本モジュールは「構造化コア → 判断の制約・志向を示す自然文」へ変換し、
LLM が policy の意図を正しく読み取れるプロンプト層を生成する。

同じ構造化コアは quant の `optimize_portfolio` の制約にも使われる（ADR-013 二重活用）。
本モジュールは「policy → 文」の担当。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, cast

_FloatLike = str | int | float


def _parse_json_or_value(v: object) -> Any:  # noqa: ANN401
    """JSON 文字列か、すでに Python オブジェクトか、どちらでも受け取れるパーサ。

    DB の TEXT 列は JSON 文字列で格納されるが、テスト等では dict/list を
    直接渡されることがあるため、文字列なら json.loads し、それ以外はそのまま返す。
    """
    if isinstance(v, str):
        try:
            return json.loads(v)
        except (json.JSONDecodeError, ValueError):
            return v
    return v


def compile_policy(policy: Mapping[str, object] | None) -> str:
    """policy 行を POLICY 層の自然文に整形する（advisor.md §3・spec §3.2）。

    policy が None（未設定）なら「方針はまだ設定されていない。対話で引き出す」旨の
    1 段落を返す（チャット初回でも壊れない）。

    整形ルール（spec §3.2）:
    - risk_tolerance / time_horizon → 「リスク許容度は高め・短〜中期」
    - target_cash_ratio / max_position_weight / sector_caps → ×100 して % で文章化（DB は 0..1）
    - target_return → 「目標リターンは高め」
    - no_leverage(1/true) → 「信用・レバレッジは使わない。
      個別の全損（ゼロカット）は受容するが借金は負わない」
    - exclusions → 「次は除外: …」
    - rationale → 末尾に理念として差す
    - 値が None/空のキーは文に含めない（部分欠損で壊れない）
    """
    if policy is None:
        return (
            "投資方針（policy）はまだ設定されていない。"
            "対話を通じてユーザーのリスク許容度・時間軸・制約を引き出し、"
            "方針を一緒に作ること。"
        )

    lines: list[str] = []

    # --- リスク許容度・時間軸 ---
    risk = policy.get("risk_tolerance")
    horizon = policy.get("time_horizon")
    if risk or horizon:
        parts: list[str] = []
        if risk:
            parts.append(f"リスク許容度は{risk}")
        if horizon:
            parts.append(f"投資時間軸は{horizon}期")
        lines.append("・" + "、".join(parts) + "。")

    # --- 目標リターン ---
    target_return = policy.get("target_return")
    if target_return is not None:
        pct = float(cast(_FloatLike, target_return)) * 100
        lines.append(f"・目標リターンは年率 {pct:.0f}% 程度を想定する。")

    # --- 現金バッファ ---
    cash_ratio = policy.get("target_cash_ratio")
    if cash_ratio is not None:
        pct = float(cast(_FloatLike, cash_ratio)) * 100
        lines.append(f"・現金バッファ {pct:.0f}% を尊重し、フルインベストメントは避ける。")

    # --- 1 銘柄上限 ---
    max_weight = policy.get("max_position_weight")
    if max_weight is not None:
        pct = float(cast(_FloatLike, max_weight)) * 100
        lines.append(f"・1 銘柄あたりの上限ウェイトは {pct:.0f}%。集中投資を制限する。")

    # --- 業種上限 ---
    sector_caps_raw = policy.get("sector_caps")
    if sector_caps_raw:
        sector_caps = _parse_json_or_value(sector_caps_raw)
        if isinstance(sector_caps, dict) and sector_caps:
            cap_parts = [f"セクター {k}: {float(v) * 100:.0f}%" for k, v in sector_caps.items()]
            lines.append("・業種上限は " + "、".join(cap_parts) + "。")

    # --- レバレッジ禁止 ---
    no_leverage = policy.get("no_leverage")
    if no_leverage:
        lines.append(
            "・信用取引・レバレッジは使わない。"
            "個別銘柄の全損（ゼロカット）は受容するが、借金を負うことは絶対にしない。"
        )

    # --- 除外銘柄 ---
    exclusions_raw = policy.get("exclusions")
    if exclusions_raw:
        exclusions = _parse_json_or_value(exclusions_raw)
        if isinstance(exclusions, list) and exclusions:
            codes_str = "・".join(str(c) for c in exclusions)
            lines.append(f"・次の銘柄は除外する: {codes_str}。")

    # --- 理念（rationale）---
    rationale = policy.get("rationale")
    if rationale:
        lines.append(f"・理念: {rationale}")

    if not lines:
        return (
            "投資方針（policy）は登録済みだが、具体的な制約・志向は設定されていない。"
            "対話を通じて方針を育てること。"
        )

    header = "## 投資方針（policy）\n"
    return header + "\n".join(lines)
