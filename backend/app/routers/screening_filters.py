"""保存スクリーニング条件の REST ルータ（CRUD・ADR-001/031）。

GET/POST /screening-filters, PUT/DELETE /screening-filters/{id}。
criteria は前方互換のため緩い dict（テクニカル軸の追加＝TODO に備える）。DB には JSON 文字列で持ち、
パース/ダンプは router の責務（repo は TEXT のまま＝backend-repo-pattern）。単一ユーザーなので
user_id は持たない（ADR-001）。
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import Connection

from app.db import repo
from app.db.engine import get_conn

router = APIRouter(tags=["screening-filters"])


class FilterIn(BaseModel):
    name: str
    criteria: dict[str, Any]  # ScreenCriteria 相当（緩い dict・前方互換）


class FilterOut(BaseModel):
    id: int
    name: str
    criteria: dict[str, Any]
    created_at: str | None = None
    updated_at: str | None = None


def _to_out(row: dict[str, Any]) -> FilterOut:
    """repo の素 dict（criteria_json は TEXT）を FilterOut に変換。壊れ JSON は 500（事前バグ）。"""
    raw = row.get("criteria_json")
    try:
        criteria = json.loads(raw) if raw else {}
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=500, detail="criteria_json の JSON が不正です。") from exc
    return FilterOut(
        id=row["id"],
        name=row["name"],
        criteria=criteria,
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


@router.get("/screening-filters", response_model=list[FilterOut])
def list_filters(conn: Connection = Depends(get_conn)) -> list[FilterOut]:
    """保存フィルタ一覧（更新日時降順）。"""
    return [_to_out(row) for row in repo.list_screening_filters(conn)]


@router.post("/screening-filters", response_model=FilterOut)
def create_filter(body: FilterIn, conn: Connection = Depends(get_conn)) -> FilterOut:
    """保存フィルタを新規作成して返す。"""
    fid = repo.insert_screening_filter(body.name, json.dumps(body.criteria, ensure_ascii=False))
    row = repo.get_screening_filter(conn, fid)
    if row is None:  # 直後に消える等は通常起きないが防御
        raise HTTPException(status_code=500, detail="作成したフィルタを取得できませんでした。")
    return _to_out(row)


@router.put("/screening-filters/{filter_id}", response_model=FilterOut)
def update_filter(
    filter_id: int, body: FilterIn, conn: Connection = Depends(get_conn)
) -> FilterOut:
    """保存フィルタを更新して返す。未存在なら 404。"""
    n = repo.update_screening_filter(
        filter_id, body.name, json.dumps(body.criteria, ensure_ascii=False)
    )
    if n == 0:
        raise HTTPException(status_code=404, detail=f"フィルタ {filter_id} は存在しません。")
    row = repo.get_screening_filter(conn, filter_id)
    assert row is not None  # 更新できた直後なので存在する
    return _to_out(row)


@router.delete("/screening-filters/{filter_id}")
def delete_filter(filter_id: int) -> dict[str, bool]:
    """保存フィルタを削除。未存在なら 404。"""
    n = repo.delete_screening_filter(filter_id)
    if n == 0:
        raise HTTPException(status_code=404, detail=f"フィルタ {filter_id} は存在しません。")
    return {"ok": True}
