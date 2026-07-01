"""Embedding アダプタ（adapters/embedding.py）の単体テスト（ADR-045/012/006/018/059）。

何を担保するか: (1) 接続未設定なら機能オフ＝embed_texts は None・embedding_enabled は False、
(2) 設定済みなら OpenAI 互換 embeddings の応答を list[list[float]] に整形して返す、(3) 入力が空なら
API を呼ばず空リストを返す。接続は DB（embedding_config）から解決する（ADR-059）ため、`_load_config`
を差し替えて DB に触れず検証する。ネットには出さず AsyncOpenAI を mock で差す（testing-strategy）。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.adapters import embedding

_CONFIG = {
    "base_url": "https://api.example.com/v1",
    "api_key": "test-key",
    "model": "text-embedding-3-small",
    "dim": 0,
}


def _run(coro):
    """async 関数を 1 回駆動するヘルパ（テスト専用・新しいイベントループで回す）。"""
    return asyncio.run(coro)


def _set_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """embedding 接続を設定済みにする（DB 解決を差し替え・機能オン）。"""
    monkeypatch.setattr(embedding, "_load_config", lambda: dict(_CONFIG))


def _set_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    """embedding 接続を未設定にする（resolve_embedding_config が None 相当）。"""
    monkeypatch.setattr(embedding, "_load_config", lambda: None)


def _mock_async_openai(monkeypatch: pytest.MonkeyPatch, vectors: list[list[float]]) -> MagicMock:
    """AsyncOpenAI を差し替え embeddings.create が指定ベクトルを返す（ネットに出ない）。"""
    resp = SimpleNamespace(data=[SimpleNamespace(embedding=v) for v in vectors])
    client = MagicMock()
    client.embeddings.create = AsyncMock(return_value=resp)
    # embed_texts は `async with AsyncOpenAI(...) as client:` で使う（クローズ保証・#24）ので、
    # client を async context manager として振る舞わせる（__aenter__ が自身を返す）。
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    factory = MagicMock(return_value=client)
    monkeypatch.setattr(embedding, "AsyncOpenAI", factory)
    return client


def test_embed_texts_disabled_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """接続未設定なら機能オフ＝None を返し embedding_enabled も False（ADR-006/018/059）。"""
    _set_unconfigured(monkeypatch)
    assert embedding.embedding_enabled() is False
    assert _run(embedding.embed_texts(["foo"])) is None


def test_embed_texts_partial_keys_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """一部キーだけ設定でも機能オフ（resolve_embedding_config が None を返す・ADR-045/059）。"""
    _set_unconfigured(monkeypatch)  # resolve_embedding_config が 3 キー未充足で None を返す相当
    assert embedding.embedding_enabled() is False
    assert _run(embedding.embed_texts(["foo"])) is None


def test_embed_texts_returns_vectors(monkeypatch: pytest.MonkeyPatch) -> None:
    """設定済みなら応答を list[list[float]] に整形して返す（ADR-045/012/059）。"""
    _set_configured(monkeypatch)
    client = _mock_async_openai(monkeypatch, [[0.1, 0.2], [0.3, 0.4]])

    result = _run(embedding.embed_texts(["foo", "bar"]))

    assert result == [[0.1, 0.2], [0.3, 0.4]]
    client.embeddings.create.assert_awaited_once_with(
        model="text-embedding-3-small", input=["foo", "bar"]
    )


def test_embed_texts_empty_input_no_api_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """入力が空なら API を呼ばず空リストを返す（ADR-045）。"""
    _set_configured(monkeypatch)
    client = _mock_async_openai(monkeypatch, [])

    result = _run(embedding.embed_texts([]))

    assert result == []
    client.embeddings.create.assert_not_awaited()
