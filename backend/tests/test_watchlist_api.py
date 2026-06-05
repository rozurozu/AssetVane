"""watchlist REST API テスト（GET/POST/DELETE /watchlist・spec §5.1）。

`client` フィクスチャ（alembic 経路で一時 SQLite）で叩く。外部・LLM は使わない。
検証対象（spec §8 テスト計画・§5.1）:
- stale の 21 日境界（ちょうど 21 日=stale でない／22 日=stale／未調査=stale）。
- CRUD（追加・重複は既存返却＝冪等・削除）。
- JOIN で company_name / last_investigated_at が乗ること。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.db import repo
from app.routers.watchlist import _is_stale

STOCK_A = {"code": "7203", "company_name": "トヨタ自動車"}
STOCK_B = {"code": "6758", "company_name": "ソニーグループ"}


# ---------------------------------------------------------------------------
# stale 境界（ユニット・21 日しきい値・L-22）
# ---------------------------------------------------------------------------


def test_stale_exactly_21_days_is_not_stale() -> None:
    """経過がちょうど 21 日なら stale ではない（「21 日超」=厳密超過・spec §5.1）。"""
    now = datetime(2026, 6, 22, 0, 0, tzinfo=UTC)
    last = (now - timedelta(days=21)).isoformat()
    assert _is_stale(last, now=now) is False


def test_stale_22_days_is_stale() -> None:
    """22 日経過は stale（再調査を促す・spec §5.1）。"""
    now = datetime(2026, 6, 23, 0, 0, tzinfo=UTC)
    last = (now - timedelta(days=22)).isoformat()
    assert _is_stale(last, now=now) is True


def test_stale_never_investigated_is_stale() -> None:
    """未調査(None)は stale（spec §5.1）。"""
    assert _is_stale(None) is True


# ---------------------------------------------------------------------------
# CRUD + JOIN（結合・client）
# ---------------------------------------------------------------------------


def test_post_then_get_watchlist_with_join(client: Any) -> None:
    """POST で追加 → GET で company_name / last_investigated_at / stale が乗る。"""
    repo.upsert_stocks([STOCK_A])
    res = client.post("/watchlist", json={"code": "7203", "note": "主力"})
    assert res.status_code == 200
    item = res.json()
    assert item["code"] == "7203"
    assert item["company_name"] == "トヨタ自動車"
    assert item["note"] == "主力"
    # 未調査なので last は None・stale=true。
    assert item["last_investigated_at"] is None
    assert item["stale"] is True

    res2 = client.get("/watchlist")
    assert res2.status_code == 200
    items = res2.json()["items"]
    assert len(items) == 1
    assert items[0]["company_name"] == "トヨタ自動車"


def test_get_watchlist_last_investigated_from_dossier(client: Any) -> None:
    """dossier がある銘柄は last_investigated_at が JOIN で乗り、最近調査なら stale=false。"""
    repo.upsert_stocks([STOCK_A])
    client.post("/watchlist", json={"code": "7203"})
    recent = datetime.now(UTC).isoformat()
    from app.db.engine import get_engine

    with get_engine().begin() as conn:
        repo.upsert_dossier(
            conn,
            code="7203",
            summary_md="要約",
            key_facts=None,
            last_investigated_at=recent,
            updated_at=recent,
        )

    items = client.get("/watchlist").json()["items"]
    assert items[0]["last_investigated_at"] == recent
    assert items[0]["stale"] is False


def test_post_watchlist_duplicate_returns_existing(client: Any) -> None:
    """重複 code の POST は既存行を 200 で返す（冪等・UNIQUE 衝突は do_nothing・spec §5.1）。"""
    repo.upsert_stocks([STOCK_A])
    first = client.post("/watchlist", json={"code": "7203", "note": "初回"}).json()
    again = client.post("/watchlist", json={"code": "7203", "note": "二回目"})
    assert again.status_code == 200
    # 既存行（初回の note）が返る。行は増えない。
    assert again.json()["id"] == first["id"]
    assert again.json()["note"] == "初回"
    assert len(client.get("/watchlist").json()["items"]) == 1


def test_delete_watchlist(client: Any) -> None:
    """DELETE /watchlist/{id} → {ok: true}・一覧から消える。"""
    repo.upsert_stocks([STOCK_A])
    created = client.post("/watchlist", json={"code": "7203"}).json()
    res = client.delete(f"/watchlist/{created['id']}")
    assert res.status_code == 200
    assert res.json() == {"ok": True}
    assert client.get("/watchlist").json()["items"] == []


def test_delete_watchlist_missing_id_is_idempotent(client: Any) -> None:
    """存在しない id の DELETE でも ok=true（冪等・spec §5.1）。"""
    res = client.delete("/watchlist/9999")
    assert res.status_code == 200
    assert res.json() == {"ok": True}
