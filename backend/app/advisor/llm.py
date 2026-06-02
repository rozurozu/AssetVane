"""LLM アダプタ（OpenAI 互換）。

ADR-012: LLM 接続は共通インターフェースのアダプタで抽象化し、`.env` の base_url / model /
api_key を差し替えるだけで OpenRouter（既定・クラウド）と Ollama（ローカル）を切り替える。
Ollama も OpenAI 互換エンドポイント（`/v1/chat/completions`）を持つため、provider 分岐は不要。

ここは「messages を送って文字列を返すだけ」のバカ運搬役に徹する。プロンプトの組み立て
（CORE / POLICY / Tool / 文脈の差し込み）は上位（router・advisor）の責務（ADR-015）。
"""

from __future__ import annotations

from openai import AsyncOpenAI

from app.config import settings

# TODO(timeout): 120s 固定。応答が遅いローカル LLM 向けに長め。将来 .env 化する
#   （例 LLM_TIMEOUT_SECONDS）。固定値で運用して不便なら設定に昇格させる。
_TIMEOUT_SECONDS = 120.0

# Ollama は認証キーが不要だが、OpenAI SDK は api_key が空だと初期化で例外を投げる。
# 空ならダミーを入れて動かす（OpenRouter 等を使うときは .env に実キーを入れる）。
_client = AsyncOpenAI(
    base_url=settings.llm_base_url,
    api_key=settings.llm_api_key or "ollama",
    timeout=_TIMEOUT_SECONDS,
)


async def complete(messages: list[dict[str, str]]) -> str:
    """messages（role/content の列）を LLM に投げ、応答テキストを返すだけ。

    数値計算や事実生成はしない（ADR-014）。Tool Calling は後続フェーズで上位が足す。

    Raises:
        openai.OpenAIError: 接続不可・タイムアウト・モデル不在など。呼び出し側（router）が
            捕まえて HTTP エラーに変換する。
    """
    # TODO(stream): いまは応答完成まで待つ非ストリーミング。将来 stream=True にして
    #   SSE で逐次返す（api.md §7「ストリーミングは実装時に決定」）。契約 {reply} は壊さず
    #   別経路（/chat/stream 等）で足せる。
    resp = await _client.chat.completions.create(
        model=settings.llm_model,
        messages=messages,  # type: ignore[arg-type]
    )
    return resp.choices[0].message.content or ""
