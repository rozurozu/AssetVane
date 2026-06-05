"""watchlist の REST ルータ（GET/POST/PATCH/DELETE /watchlist・spec §5.1 / docs/api.md §5）。

設計の真実: docs/phase-specs/phase4-spec.md §5.1・ADR-020/ADR-002/ADR-005/ADR-033。

HTTP 入出力のみを担う薄い層（ロジック・DB クエリ詳細は repo.py）。
- 読み取りは `Depends(get_conn)`、書き込み（add/remove/set_interval）は repo が自前で `begin()` を
  所有する（add_watchlist / remove_watchlist / set_watchlist_interval が W1・UNIQUE 衝突は repo
  側で do_nothing）。
- stale 判定（経過 > interval_days 日 or 未調査 → stale=true）はこの層の責務。しきい値は固定 21
  ではなく per-row の `interval_days`（銘柄ごとの調査間隔・ADR-033）。repo は
  last_investigated_at と interval_days を返すだけ（L-22）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Connection

from app.db import repo
from app.db.engine import get_conn, get_engine

router = APIRouter(tags=["watchlist"])

# stale しきい値の既定（ADR-033）。per-row の interval_days が無い行のフォールバックに使う
# （通常は repo が常に interval_days=21 以上を返すため使われない）。
_DEFAULT_INTERVAL_DAYS = 21


# ---------------------------------------------------------------------------
# Pydantic モデル（spec §5.1・TS 型 WatchlistItem と 1:1）
# ---------------------------------------------------------------------------


class WatchlistItem(BaseModel):
    """watchlist の 1 行（spec §5.1・lib/api.ts WatchlistItem と 1:1）。"""

    id: int
    code: str
    company_name: str | None = None  # stocks JOIN
    note: str | None = None
    added_at: str | None = None
    last_investigated_at: str | None = None  # stock_dossiers JOIN（一覧の「最終調査日」）
    interval_days: int  # 銘柄ごとの調査間隔（日・既定 21・stale 起点＝ADR-033）
    stale: bool  # 経過 > interval_days 日 or 未調査 → 再調査を促す（backend 算出）


class WatchlistResponse(BaseModel):
    """GET /watchlist のレスポンス（spec §5.1）。"""

    items: list[WatchlistItem]


class WatchlistInput(BaseModel):
    """POST /watchlist のリクエスト（spec §5.1）。"""

    code: str
    note: str | None = None


class WatchlistIntervalInput(BaseModel):
    """PATCH /watchlist/{code} のリクエスト（調査間隔の更新・ADR-033）。"""

    interval_days: int = Field(ge=1, description="調査間隔（日・1 以上）")


class DeleteResult(BaseModel):
    """DELETE /watchlist/{id} のレスポンス（spec §5.1）。"""

    ok: bool


# ---------------------------------------------------------------------------
# stale 算出・行整形（ルータ層の責務・L-22）
# ---------------------------------------------------------------------------


def _is_stale(
    last_investigated_at: str | None,
    interval_days: int = _DEFAULT_INTERVAL_DAYS,
    *,
    now: datetime | None = None,
) -> bool:
    """経過が interval_days 日より古い、または未調査(None)なら True（spec §5.1・ADR-033）。

    しきい値は固定 21 ではなく per-row の `interval_days`（銘柄ごとの調査間隔）。
    境界: 経過がちょうど interval_days 日なら stale ではない（「interval_days 日超」=厳密超過）。
    パース不能・None は未調査とみなし stale=true（再調査を促す側に倒す）。
    """
    if last_investigated_at is None:
        return True
    try:
        last = datetime.fromisoformat(last_investigated_at)
    except ValueError:
        return True
    # 文字列が tz-naive なら UTC とみなして比較する（保存は ISO8601・UTC 起点）。
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    current = now or datetime.now(UTC)
    return (current - last).total_seconds() > interval_days * 86400


def _row_to_item(row: dict[str, Any]) -> WatchlistItem:
    """list_watchlist の生 dict に stale を付与して WatchlistItem に整形（spec §5.1・ADR-033）。

    stale は per-row の interval_days 基準で算出する（repo は interval_days を常に非 NULL で返す）。
    """
    last = row.get("last_investigated_at")
    interval_days = int(row.get("interval_days") or _DEFAULT_INTERVAL_DAYS)
    return WatchlistItem(
        id=int(row["id"]),
        code=row["code"],
        company_name=row.get("company_name"),
        note=row.get("note"),
        added_at=row.get("added_at"),
        last_investigated_at=last,
        interval_days=interval_days,
        stale=_is_stale(last, interval_days),
    )


# ---------------------------------------------------------------------------
# エンドポイント
# ---------------------------------------------------------------------------


@router.get("/watchlist", response_model=WatchlistResponse)
def get_watchlist(conn: Connection = Depends(get_conn)) -> WatchlistResponse:
    """watchlist を company_name・last_investigated_at・interval_days・stale 付きで返す（§5.1）。

    last_investigated_at は stock_dossiers JOIN（repo）、stale は per-row の interval_days を
    しきい値に算出（ADR-033）。
    """
    rows = repo.list_watchlist(conn)
    return WatchlistResponse(items=[_row_to_item(r) for r in rows])


@router.post("/watchlist", response_model=WatchlistItem)
def add_to_watchlist(req: WatchlistInput) -> WatchlistItem:
    """watchlist に 1 銘柄を追加し、その行を返す（spec §5.1）。

    UNIQUE(code) 衝突は repo が do_nothing で既存行を返す（重複でもエラーにせず既存を返す＝
    spec §5.1「重複として扱う」の解釈。冪等で 200・追加行/既存行ともに WatchlistItem）。
    add_watchlist は watchlist 列だけ返す（company_name/last_investigated_at は無い）ため、
    JOIN 付きの list_watchlist から該当 code の行を読み直して WatchlistItem を完成させる。
    """
    added = repo.add_watchlist(req.code, req.note)
    code = added.get("code", req.code)
    with get_engine().connect() as conn:
        joined = next((r for r in repo.list_watchlist(conn) if r["code"] == code), None)
    # 念のため JOIN 行が取れなければ素の追加行で組む（FK 前提では常に取れる）。
    return _row_to_item(joined or added)


@router.patch("/watchlist/{code}", response_model=WatchlistItem)
def update_watchlist_interval(code: str, req: WatchlistIntervalInput) -> WatchlistItem:
    """watchlist の銘柄の調査間隔（interval_days）を更新し、更新後の行を返す（ADR-033）。

    interval_days は per-row の stale しきい値（銘柄ごとの調査間隔）。入力検証（>= 1）は
    Pydantic（Field ge=1）が担う。存在しない code は 404 に翻訳する（repo は影響行 0 で静かに
    終わるので、更新後の読み直しで該当行が無ければ未登録と判断する）。
    更新後は JOIN 付きの list_watchlist から該当 code を読み直して WatchlistItem を完成させる。
    """
    repo.set_watchlist_interval(code, req.interval_days)
    with get_engine().connect() as conn:
        joined = next((r for r in repo.list_watchlist(conn) if r["code"] == code), None)
    if joined is None:
        raise HTTPException(status_code=404, detail=f"watchlist に code={code} がありません。")
    return _row_to_item(joined)


@router.delete("/watchlist/{watchlist_id}", response_model=DeleteResult)
def delete_from_watchlist(watchlist_id: int) -> DeleteResult:
    """watchlist の id 行を削除する（spec §5.1・存在しない id でも冪等に ok=true）。"""
    repo.remove_watchlist(watchlist_id)
    return DeleteResult(ok=True)
