"""Embedding アダプタ（adapters/embedding.py）の単体テスト（ADR-045/012/006/018）。

何を担保するか: (1) 3 キーのいずれかが未設定なら機能オフ＝embed_texts は None・
embedding_enabled は False、(2) 3 キー設定時は OpenAI 互換 embeddings の応答を
list[list[float]] に整形して返す、(3) 入力が空なら API を呼ばず空リストを返す。
ネットには出さず AsyncOpenAI を mock で差し替える（testing-strategy）。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.adapters import embedding
from app.config import settings


def _run(coro):
    """async 関数を 1 回駆動するヘルパ（テスト専用・新しいイベントループで回す）。"""
    return asyncio.run(coro)


def _set_embedding_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """embedding 3 キーを設定済みにする（機能オン）。"""
    monkeypatch.setattr(settings, "embedding_base_url", "https://api.example.com/v1")
    monkeypatch.setattr(settings, "embedding_api_key", "test-key")
    monkeypatch.setattr(settings, "embedding_model", "text-embedding-3-small")


def _mock_async_openai(monkeypatch: pytest.MonkeyPatch, vectors: list[list[float]]) -> MagicMock:
    """AsyncOpenAI を差し替え embeddings.create が指定ベクトルを返す（ネットに出ない）。"""
    resp = SimpleNamespace(data=[SimpleNamespace(embedding=v) for v in vectors])
    client = MagicMock()
    client.embeddings.create = AsyncMock(return_value=resp)
    factory = MagicMock(return_value=client)
    monkeypatch.setattr(embedding, "AsyncOpenAI", factory)
    return client


def test_embed_texts_disabled_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """3 キーが未設定なら機能オフ＝None を返し embedding_enabled も False（ADR-006/018）。"""
    monkeypatch.setattr(settings, "embedding_base_url", "")
    monkeypatch.setattr(settings, "embedding_api_key", "")
    monkeypatch.setattr(settings, "embedding_model", "")
    assert embedding.embedding_enabled() is False
    assert _run(embedding.embed_texts(["foo"])) is None


def test_embed_texts_partial_keys_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """一部キーだけ設定でも機能オフ（3 キー全て揃って初めて有効・ADR-045）。"""
    monkeypatch.setattr(settings, "embedding_base_url", "https://api.example.com/v1")
    monkeypatch.setattr(settings, "embedding_api_key", "")
    monkeypatch.setattr(settings, "embedding_model", "text-embedding-3-small")
    assert embedding.embedding_enabled() is False
    assert _run(embedding.embed_texts(["foo"])) is None


def test_embed_texts_returns_vectors(monkeypatch: pytest.MonkeyPatch) -> None:
    """3 キー設定時は応答を list[list[float]] に整形して返す（ADR-045/012）。"""
    _set_embedding_keys(monkeypatch)
    client = _mock_async_openai(monkeypatch, [[0.1, 0.2], [0.3, 0.4]])

    result = _run(embedding.embed_texts(["foo", "bar"]))

    assert result == [[0.1, 0.2], [0.3, 0.4]]
    client.embeddings.create.assert_awaited_once_with(
        model="text-embedding-3-small", input=["foo", "bar"]
    )


def test_embed_texts_empty_input_no_api_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """入力が空なら API を呼ばず空リストを返す（ADR-045）。"""
    _set_embedding_keys(monkeypatch)
    client = _mock_async_openai(monkeypatch, [])

    result = _run(embedding.embed_texts([]))

    assert result == []
    client.embeddings.create.assert_not_awaited()
