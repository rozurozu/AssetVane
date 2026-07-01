"""Embedding アダプタ（OpenAI 互換・ニュース意味検索の段階A 基盤）。

設計の真実: ADR-045（ニュース意味検索）・ADR-012（LLM は OpenAI 互換 1 本で抽象化）・
ADR-010（外部 API はアダプタ越し・直結ハードコード禁止）・ADR-059（接続設定を env→DB+WebUI）。

embedding プロバイダは chat と同型で OpenAI 互換 1 本のみ（ADR-012）。base_url / api_key / model は
DB（embedding_config・/settings で編集）から解決する（ADR-059）。3 キーのいずれかが未設定なら静かに
機能オフ＝`embed_texts` は None を返す（ADR-006/018）。timeout は接続パラメータとして env 据え置き。

このモジュールは「テキスト列を embedding ベクトル列に変換するだけ」のバカ運搬役に徹する。
格納（BLOB＋vec_distance_cosine）・検索・ジョブ駆動は後続 wave（repo/service/job）の責務。
API 失敗/タイムアウトは握らず raise する（上位＝job/service が握って通知に翻訳する方針）。
"""

from __future__ import annotations

from openai import AsyncOpenAI

from app.config import settings
from app.db.engine import get_engine
from app.services.llm_config import resolve_embedding_config

# 設定差し替え（DB 編集・テストの seed）を確実に拾うため、クライアントは関数内で都度生成する
# （chat の llm.py はモジュールレベルで持つが、こちらは未設定で機能オフのため遅延生成が素直）。


def _load_config() -> dict[str, object] | None:
    """DB から embedding 接続を読む（読み取り接続を短く開閉・ADR-059）。"""
    with get_engine().connect() as conn:
        return resolve_embedding_config(conn)


def embedding_enabled() -> bool:
    """embedding 機能が有効か（base_url / api_key / model の 3 キーが揃っているか・ADR-045/059）。

    後続 wave（service/job）が呼び出し前の早期 skip 判定に使えるよう公開する。
    """
    return _load_config() is not None


def embedding_model() -> str:
    """設定済みの embedding model 名を返す（未設定なら ""・ADR-059）。

    呼び出し側（embed_news/embed_themes/news）が「どの model で埋め込んだか」の記録・再埋め込み判定
    に使う（旧 settings.embedding_model の後継・DB 解決）。
    """
    config = _load_config()
    return str(config["model"]) if config is not None else ""


async def embed_texts(texts: list[str]) -> list[list[float]] | None:
    """テキスト列を OpenAI 互換 embeddings API でベクトル列に変換する（ADR-045/012/059）。

    Args:
        texts: 埋め込むテキスト列。空リストなら API を呼ばず空リストを返す。

    Returns:
        各テキストに対応する embedding（list[float]）のリスト。接続未設定なら None（機能オフ・
        ADR-006/018）。

    Raises:
        openai.OpenAIError: 接続不可・タイムアウト・モデル不在など。握らず投げる
            （上位＝job/service が握って通知/skip に翻訳する）。
    """
    config = _load_config()
    if config is None:
        return None
    if not texts:
        return []

    # 都度生成のまま `async with` でクローズまで面倒を見る（HTTP コネクションプールの後始末漏れを
    # 防ぐ・#24）。埋め込みは夜間バッチ/貼付時に多数回呼ばれるため未クローズは緩やかな leak になる。
    async with AsyncOpenAI(
        base_url=str(config["base_url"]),
        api_key=str(config["api_key"]),
        timeout=settings.embedding_timeout_seconds,
    ) as client:
        resp = await client.embeddings.create(model=str(config["model"]), input=texts)
    return [list(item.embedding) for item in resp.data]
