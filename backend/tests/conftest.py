"""pytest 共通フィクスチャ。

各テストは tmp_path の使い捨て SQLite を使い、本物の DB（data/assetvane.db）に触れない。
スキーマは create_schema（metadata 直接）で速く用意する。alembic 経路は test_migrations で別途検証。
ネットには出ない（J-Quants 取得は呼ばない。正規化はサンプル dict で検証）。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.config import settings
from app.db import engine as db_engine

# テスト用 LLM 面設定の seed 値（ADR-058）。本番はシードしない（確定4）が、テストは LLM 経路を
# openai（モック可能）に固定するためダミー provider＋4 面を入れる。これがないと resolve_face が
# FaceNotConfiguredError で落ち、/chat は 503・nightly 等は skip になる。codex subprocess を避ける
# 狙いは旧 llm_provider_* monkeypatch と同じ（.env 非依存・ネットに出ない）。
_SEED_PROVIDER_NAME = "test-openai"
_SEED_BASE_URL = "https://test.invalid/v1"
_SEED_MODEL = "test-model"


def seed_llm_config() -> None:
    """temp DB に openai provider 1 行＋4 面（chat/nightly/dossier/tagger）を seed する（ADR-058）。

    現エンジン（差し替え済み database_path）に対して書く。冪等（既に在れば無視）。
    """
    from sqlalchemy import insert, select

    from app.db.engine import get_engine
    from app.db.schema import llm_face_config, llm_providers
    from app.services.llm_config import FACES

    with get_engine().begin() as conn:
        existing = conn.execute(
            select(llm_providers.c.id).where(llm_providers.c.name == _SEED_PROVIDER_NAME)
        ).first()
        if existing is None:
            pid = conn.execute(
                insert(llm_providers).values(
                    name=_SEED_PROVIDER_NAME,
                    base_url=_SEED_BASE_URL,
                    api_key="test-key",
                    default_model=_SEED_MODEL,
                )
            ).inserted_primary_key[0]
        else:
            pid = existing[0]
        for face in FACES:
            already = conn.execute(
                select(llm_face_config.c.face).where(llm_face_config.c.face == face)
            ).first()
            if already is None:
                conn.execute(
                    insert(llm_face_config).values(face=face, provider_id=pid, model=_SEED_MODEL)
                )


@pytest.fixture
def temp_db(tmp_path, monkeypatch) -> Iterator[None]:
    """settings.database_path を一時ファイルに差し替え、スキーマ＋LLM 面設定を用意する。"""
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(settings, "database_path", str(db_file))
    db_engine.reset_engine()
    db_engine.create_schema()
    seed_llm_config()  # LLM 経路を openai に固定（旧 llm_provider_* monkeypatch の後継・ADR-058）
    yield
    db_engine.reset_engine()


@pytest.fixture
def client(tmp_path, monkeypatch) -> Iterator[object]:
    """TestClient（lifespan 発火 = init_db）。一時 SQLite を alembic 経路で用意する。

    `temp_db`（create_schema）には依存しない。lifespan の `init_db()`（alembic upgrade）が
    スキーマを作るので、create_schema と二重に作ると `op.create_table`（0002/0003）が
    "table already exists" で落ちるため。本番と同じ alembic 経路でスキーマを得る。

    LLM 面設定は seed_llm_config で openai に固定する（ADR-058）。これがないと resolve_face が
    FaceNotConfiguredError で落ち、`/chat` が 503 になる。codex_engine の実 subprocess 起動も避ける
    （旧 llm_provider_*=openai monkeypatch の後継・.env 非依存・ネットに出ない）。
    """
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(settings, "database_path", str(db_file))
    db_engine.reset_engine()

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        seed_llm_config()  # lifespan(init_db=alembic) でスキーマができた後に seed する
        yield c

    db_engine.reset_engine()
