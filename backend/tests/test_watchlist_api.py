"""watchlist REST API テスト（GET/POST/PATCH/DELETE /watchlist・spec §5.1・ADR-033）。

`client` フィクスチャ（alembic 経路で一時 SQLite）で叩く。外部・LLM は使わない。
検証対象（spec §8 テスト計画・§5.1・ADR-033）:
- stale の境界（per-row interval_days・既定 21 のちょうど境界／超過／未調査／短間隔・長間隔）。
- CRUD（追加・重複は既存返却＝冪等・削除）。
- PATCH で interval_days を更新でき、更新後の stale が新間隔で再算出されること。
- JOIN で company_name / last_investigated_at / interval_days が乗ること。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.db import repo
from app.routers.watchlist import _is_stale

STOCK_A = {"code": "7203", "company_name": "トヨタ自動車"}
STOCK_B = {"code": "6758", "company_name": "ソニーグループ"}


# ---------------------------------------------------------------------------
# stale 境界（ユニット・per-row interval_days しきい値・ADR-033）
# ---------------------------------------------------------------------------


def test_stale_exactly_interval_days_is_not_stale() -> None:
    """経過がちょうど interval_days(=21)なら stale ではない（厳密超過・spec §5.1・ADR-033）。"""
    now = datetime(2026, 6, 22, 0, 0, tzinfo=UTC)
    last = (now - timedelta(days=21)).isoformat()
    assert _is_stale(last, 21, now=now) is False


def test_stale_over_interval_days_is_stale() -> None:
    """interval_days を超えた経過は stale（再調査を促す・spec §5.1・ADR-033）。"""
    now = datetime(2026, 6, 23, 0, 0, tzinfo=UTC)
    last = (now - timedelta(days=22)).isoformat()
    assert _is_stale(last, 21, now=now) is True


def test_stale_short_interval_makes_recent_stale() -> None:
    """interval=1 の銘柄は 2 日前の調査でも stale（per-row 短間隔・ADR-033）。"""
    now = datetime(2026, 6, 5, 0, 0, tzinfo=UTC)
    last = (now - timedelta(days=2)).isoformat()
    assert _is_stale(last, 1, now=now) is True


def test_stale_long_interval_keeps_recent_fresh() -> None:
    """interval=30 の銘柄は 2 日前の調査なら not stale（per-row 長間隔・ADR-033）。"""
    now = datetime(2026, 6, 5, 0, 0, tzinfo=UTC)
    last = (now - timedelta(days=2)).isoformat()
    assert _is_stale(last, 30, now=now) is False


def test_stale_never_investigated_is_stale() -> None:
    """未調査(None)は stale（どの interval_days でも・spec §5.1）。"""
    assert _is_stale(None, 30) is True


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
    # 未調査なので last は None・stale=true。interval_days は既定 21。
    assert item["last_investigated_at"] is None
    assert item["interval_days"] == 21
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


# ---------------------------------------------------------------------------
# PATCH /watchlist/{code}（調査間隔の更新・ADR-033）
# ---------------------------------------------------------------------------


def test_patch_watchlist_interval_updates_and_returns_row(client: Any) -> None:
    """PATCH で interval_days を更新でき、更新後の行（WatchlistItem）が返る（ADR-033）。"""
    repo.upsert_stocks([STOCK_A])
    client.post("/watchlist", json={"code": "7203"})
    res = client.patch("/watchlist/7203", json={"interval_days": 7})
    assert res.status_code == 200
    item = res.json()
    assert item["code"] == "7203"
    assert item["interval_days"] == 7
    # GET でも反映される（永続化）。
    items = client.get("/watchlist").json()["items"]
    assert items[0]["interval_days"] == 7


def test_patch_watchlist_interval_recomputes_stale(client: Any) -> None:
    """interval を短くすると、最近の調査でも stale=true に再算出される（per-row 基準・ADR-033）。"""
    repo.upsert_stocks([STOCK_A])
    client.post("/watchlist", json={"code": "7203"})
    # 2 日前に調査済みにする。
    two_days_ago = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    from app.db.engine import get_engine

    with get_engine().begin() as conn:
        repo.upsert_dossier(
            conn,
            code="7203",
            summary_md="要約",
            key_facts=None,
            last_investigated_at=two_days_ago,
            updated_at=two_days_ago,
        )
    # 既定 21 日では not stale。
    assert client.get("/watchlist").json()["items"][0]["stale"] is False
    # interval=1 に縮めると stale=true。
    patched = client.patch("/watchlist/7203", json={"interval_days": 1}).json()
    assert patched["stale"] is True


def test_patch_watchlist_interval_rejects_below_one(client: Any) -> None:
    """interval_days < 1 は 422（Pydantic 入力検証・ADR-033）。"""
    repo.upsert_stocks([STOCK_A])
    client.post("/watchlist", json={"code": "7203"})
    res = client.patch("/watchlist/7203", json={"interval_days": 0})
    assert res.status_code == 422


def test_patch_watchlist_interval_missing_code_is_404(client: Any) -> None:
    """未登録 code の PATCH は 404（存在確認は router の責務・ADR-033）。"""
    res = client.patch("/watchlist/9999", json={"interval_days": 7})
    assert res.status_code == 404
