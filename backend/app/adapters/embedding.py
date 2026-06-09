"""Embedding アダプタ（OpenAI 互換・ニュース意味検索の段階A 基盤）。

設計の真実: ADR-045（ニュース意味検索）・ADR-012（LLM は OpenAI 互換 1 本で抽象化）・
ADR-010（外部 API はアダプタ越し・直結ハードコード禁止）。

embedding プロバイダは chat と同型で OpenAI 互換 1 本のみ（ADR-012）。`.env` の
embedding_base_url / embedding_api_key / embedding_model を差し替えれば openai 直・localllm を
吸収する（Anthropic/Voyage ブランチは作らない）。3 キーのいずれかが未設定なら静かに機能オフ
＝`embed_texts` は None を返す（llm_api_key 未設定と同じ作法・ADR-006/018）。

このモジュールは「テキスト列を embedding ベクトル列に変換するだけ」のバカ運搬役に徹する。
格納（BLOB＋vec_distance_cosine）・検索・ジョブ駆動は後続 wave（repo/service/job）の責務。
API 失敗/タイムアウトは握らず raise する（上位＝job/service が握って通知に翻訳する方針）。
"""

from __future__ import annotations

from openai import AsyncOpenAI

from app.config import settings

# 設定差し替え（テストの monkeypatch）を確実に拾うため、クライアントは関数内で都度生成する
# （chat の llm.py はモジュールレベルで持つが、こちらは未設定で機能オフのため遅延生成が素直）。


def embedding_enabled() -> bool:
    """embedding 機能が有効か（base_url / api_key / model の 3 キーが揃っているか・ADR-045）。

    後続 wave（service/job）が呼び出し前の早期 skip 判定に使えるよう公開する。
    """
    return bool(
        settings.embedding_base_url and settings.embedding_api_key and settings.embedding_model
    )


async def embed_texts(texts: list[str]) -> list[list[float]] | None:
    """テキスト列を OpenAI 互換 embeddings API でベクトル列に変換する（ADR-045/012）。

    Args:
        texts: 埋め込むテキスト列。空リストなら API を呼ばず空リストを返す。

    Returns:
        各テキストに対応する embedding（list[float]）のリスト。3 キーのいずれかが未設定なら
        None（機能オフ・ADR-006/018）。

    Raises:
        openai.OpenAIError: 接続不可・タイムアウト・モデル不在など。握らず投げる
            （上位＝job/service が握って通知/skip に翻訳する）。
    """
    if not embedding_enabled():
        return None
    if not texts:
        return []

    client = AsyncOpenAI(
        base_url=settings.embedding_base_url,
        api_key=settings.embedding_api_key,
        timeout=settings.embedding_timeout_seconds,
    )
    resp = await client.embeddings.create(model=settings.embedding_model, input=texts)
    return [list(item.embedding) for item in resp.data]
