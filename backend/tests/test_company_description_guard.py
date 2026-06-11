"""company_descriptions の dossier 優先ガード検証（テーマタグ段階C・ADR-050/056）。

担保: EDINET 書き込み（upsert_company_description_edinet・protect_dossier=True）は既存
source='dossier' を上書きしない／既存が無ければ書く／edinet→edinet はテキスト変化で更新する。
段階B の dossier 書き込み（W2・無ガード）は無条件で edinet を上書きする（dossier が勝つ）。
"""

from __future__ import annotations

from typing import Any

from app.db import repo
from app.db.engine import get_engine


def _edinet_row(code: str, text: str, disclosed: str = "2025-03-31") -> dict[str, Any]:
    return {
        "market": "JP",
        "code": code,
        "source": "edinet",
        "description_text": text,
        "disclosed_date": disclosed,
        "doc_id": f"S{code}",
        "fetched_at": "2026-06-11T00:00:00+00:00",
    }


def _read(code: str) -> dict[str, Any] | None:
    with get_engine().connect() as conn:
        return repo.get_company_description(conn, "JP", code)


def test_edinet_writes_when_empty(temp_db) -> None:
    """既存行が無ければ EDINET 書き込みが入る（普通の新規 UPSERT）。"""
    n = repo.upsert_company_description_edinet(_edinet_row("72030", "半導体製造装置を作る"))
    assert n == 1
    row = _read("72030")
    assert row and row["source"] == "edinet"
    assert row["description_text"] == "半導体製造装置を作る"


def test_edinet_does_not_overwrite_dossier(temp_db) -> None:
    """既存が dossier（調査済みオーバーレイ）なら EDINET は上書きしない（dossier ⊇ EDINET）。"""
    # 段階B 相当: dossier をまず焼く（W2・無ガード）。
    with get_engine().begin() as conn:
        repo.upsert_company_description_tx(
            conn,
            market="JP",
            code="72030",
            source="dossier",
            description_text="ドシエ要約（ニュース＋財務込み）",
        )
    # 段階C: EDINET 書き込みは protect_dossier=True で弾かれる（影響 0 行）。
    n = repo.upsert_company_description_edinet(_edinet_row("72030", "EDINET の事業の内容"))
    assert n == 0
    row = _read("72030")
    assert row and row["source"] == "dossier"
    assert row["description_text"] == "ドシエ要約（ニュース＋財務込み）"


def test_edinet_updates_edinet_on_text_change(temp_db) -> None:
    """edinet→edinet はテキスト変化で更新し、同一テキストは据え置き（fetched_at の意味を保つ）。"""
    repo.upsert_company_description_edinet(_edinet_row("67580", "旧テキスト"))
    # 同一テキストは据え置き（0 行）。
    assert repo.upsert_company_description_edinet(_edinet_row("67580", "旧テキスト")) == 0
    # テキスト変化は更新（1 行）。
    assert repo.upsert_company_description_edinet(_edinet_row("67580", "新テキスト")) == 1
    row = _read("67580")
    assert row and row["description_text"] == "新テキスト"


def test_dossier_overwrites_edinet(temp_db) -> None:
    """段階B の dossier 書き込みは無条件で勝つ（edinet ベースラインを調査済みで上書き）。"""
    repo.upsert_company_description_edinet(_edinet_row("99840", "EDINET ベースライン"))
    with get_engine().begin() as conn:
        repo.upsert_company_description_tx(
            conn,
            market="JP",
            code="99840",
            source="dossier",
            description_text="調査済みドシエ",
        )
    row = _read("99840")
    assert row and row["source"] == "dossier"
    assert row["description_text"] == "調査済みドシエ"
