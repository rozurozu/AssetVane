"""LLM アダプタ（OpenAI 互換・Tool Calling 対応）。

設計の真実: docs/phase-specs/phase3-spec.md §4.3・§7.1・ADR-012/028。

ADR-012: LLM 接続は共通インターフェースのアダプタで抽象化し、`.env` の base_url / model /
api_key を差し替えるだけで OpenRouter（既定・クラウド）と Ollama（ローカル）を切り替える。
Ollama も OpenAI 互換エンドポイント（`/v1/chat/completions`）を持つため provider 分岐は不要。

ここは「messages（＋tools）を送って応答（テキスト or tool_calls）を返すだけ」のバカ運搬役に
徹する。プロンプト組み立て（CORE/POLICY/Tool/文脈の差し込み）も Tool の dispatch も上位の責務
（ADR-015・ADR-014）。本モジュールは計算をしない。

§7.1 コストガードレール（ADR-028）: 呼び出し前に当月累計コストを見て `block` なら止め、
呼び出し後に OpenRouter の実コスト（usage.cost）を `llm_usage` に計上する。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from openai import AsyncOpenAI, omit
from pydantic import BaseModel

from app.config import settings
from app.db import repo
from app.db.engine import get_engine

logger = logging.getLogger(__name__)


class ToolCall(BaseModel):
    """LLM が要求した 1 件の Tool 呼び出し（OpenAI 互換・spec §4.3）。"""

    id: str  # OpenAI 互換の tool_call_id（dispatch が tool ロール返信に使う）
    name: str  # Tool 名（registry のキー）
    arguments: dict[str, object]  # json.loads 済みの引数 dict


class LLMResponse(BaseModel):
    """LLM の 1 ターン応答（spec §4.3）。tool_calls が空なら最終応答。"""

    content: str | None  # テキスト応答（tool_calls 時は None のことがある）
    tool_calls: list[ToolCall]  # 空なら最終応答


class CostGuardError(RuntimeError):
    """LLM 月額コスト上限超過で block した（ADR-028・spec §7.1）。

    `mode="block"` のときのみ raise する。呼び出し側（router/nightly）が握って
    スキップ理由を記録・通知する。
    """


# Ollama は認証キーが不要だが、OpenAI SDK は api_key が空だと初期化で例外を投げる。
# 空ならダミーを入れて動かす（OpenRouter 等を使うときは .env に実キーを入れる）。
# 指数バックオフ付きリトライ・タイムアウトは SDK の max_retries / timeout に委譲する
# （spec §4.3・§7・data-arch §3.3）。
_client = AsyncOpenAI(
    base_url=settings.llm_base_url,
    api_key=settings.llm_api_key or "ollama",
    timeout=settings.llm_timeout_seconds,
    max_retries=settings.llm_max_retries,
)


def _current_month() -> str:
    """当月（'YYYY-MM'）を UTC で返す（当月累計コスト判定の起点・spec §7.1）。"""
    return datetime.now(UTC).strftime("%Y-%m")


def _check_cost_guard() -> None:
    """呼び出し前のコストガード判定（spec §7.1・ADR-028）。

    `block`: 当月累計が上限以上なら API を呼ばず `CostGuardError` を raise。
    `warn`: 上限超過を `logger.warning` で記録するが呼び出しは止めない（既定）。
    `off`: 何もしない（計上のみ別途行う）。
    判定で DB を引けない等の失敗は本体を止めない（呼び出しを許す）。
    """
    mode = settings.llm_cost_guard_mode
    if mode == "off":
        return
    try:
        with get_engine().connect() as conn:
            total = repo.sum_llm_cost_month(conn, _current_month())
    except Exception:
        # コスト判定の失敗で LLM を止めない（ガードは best-effort・spec §7.1）。
        logger.exception("コストガード判定に失敗（呼び出しは続行する）")
        return

    if total < settings.llm_cost_limit_usd:
        return

    if mode == "block":
        raise CostGuardError(
            f"LLM 月額コスト上限超過: 当月累計 ${total:.2f} >= 上限 "
            f"${settings.llm_cost_limit_usd:.2f}（block）"
        )
    # mode == "warn"（既定）: 止めずに警告だけ残す。
    # Discord 通知は夜間バッチ notify_cost_warn へ移管（advisor→batch 逆流回避・ADR-028）。
    # 画面バナーは /health の llm_cost を frontend が読む。ここはログのみ。
    logger.warning(
        "LLM 月額コスト上限超過（warn・続行）: 当月累計 $%.2f >= 上限 $%.2f",
        total,
        settings.llm_cost_limit_usd,
    )


def _record_usage(usage: object, *, source: str) -> None:
    """OpenRouter のレスポンス usage を `llm_usage` に計上する（spec §7.1・ADR-028）。

    cost は OpenRouter の `usage.cost`（無ければ 0.0・Ollama は cost 無し）。
    計上失敗で LLM 応答を壊さない（try/except で握ってログのみ）。
    """
    if usage is None:
        return
    try:
        # OpenRouter は usage.cost を返す。SDK の型に無いので getattr / model_extra で拾う。
        cost = getattr(usage, "cost", None)
        if cost is None:
            extra = getattr(usage, "model_extra", None)
            if isinstance(extra, dict):
                cost = extra.get("cost")
        tokens_in = getattr(usage, "prompt_tokens", None)
        tokens_out = getattr(usage, "completion_tokens", None)
        with get_engine().begin() as conn:
            repo.insert_llm_usage(
                conn,
                source=source,
                model=settings.llm_model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=float(cost) if cost is not None else 0.0,
            )
    except Exception:
        logger.exception("llm_usage の計上に失敗（LLM 応答は返す）")


def _parse_tool_calls(message: object) -> list[ToolCall]:
    """OpenAI レスポンスの message.tool_calls を ToolCall 列に詰める（spec §4.3）。

    function.arguments（JSON 文字列）は json.loads して dict にする。壊れていたら {}。
    """
    raw_calls = getattr(message, "tool_calls", None) or []
    calls: list[ToolCall] = []
    for tc in raw_calls:
        func = getattr(tc, "function", None)
        if func is None:
            continue
        raw_args = getattr(func, "arguments", None) or "{}"
        try:
            parsed = json.loads(raw_args)
        except (TypeError, ValueError):
            parsed = {}
        if not isinstance(parsed, dict):
            parsed = {}
        calls.append(
            ToolCall(
                id=str(getattr(tc, "id", "")),
                name=str(getattr(func, "name", "")),
                arguments=parsed,
            )
        )
    return calls


async def complete(
    messages: list[dict[str, object]],
    *,
    tools: list[dict[str, object]] | None = None,
    stream: bool = False,
    source: str = "chat",
) -> LLMResponse:
    """messages（＋tools）を LLM に投げ、テキストか tool_calls を返す（spec §4.3）。

    数値計算や事実生成はしない（ADR-014）。Tool の dispatch は上位（router/nightly）の責務。

    Args:
        messages: OpenAI 形式の会話列（system/user/assistant/tool）。
        tools: OpenAI tools スキーマ（registry.openai_tools が供給）。None なら無効。
        stream: 将来用。Phase 3 は False 固定（非ストリーミング・spec §4.3）。
        source: `llm_usage` の呼び出し文脈タグ（"chat"/"nightly" 等）。

    Raises:
        CostGuardError: mode="block" かつ当月累計が上限以上（spec §7.1）。API は呼ばない。
        openai.OpenAIError: 接続不可・タイムアウト（リトライ後）・モデル不在など。呼び出し側が
            捕まえて HTTP エラー / journal スキップに変換する。
    """
    # 呼び出し前: コストガード（block なら CostGuardError で止める・spec §7.1）。
    _check_cost_guard()

    # tools 指定時のみ tool_choice="auto"。未指定は omit（送らない）で従来の純テキスト応答。
    resp = await _client.chat.completions.create(
        model=settings.llm_model,
        messages=messages,  # type: ignore[arg-type]
        tools=tools if tools else omit,  # type: ignore[arg-type]
        tool_choice="auto" if tools else omit,
    )

    # 呼び出し後: 実コストを計上（失敗しても応答は返す・spec §7.1）。
    _record_usage(getattr(resp, "usage", None), source=source)

    message = resp.choices[0].message
    return LLMResponse(
        content=message.content,
        tool_calls=_parse_tool_calls(message),
    )
