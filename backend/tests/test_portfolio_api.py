"""ポートフォリオ・資産概要 API のテスト（Phase 2・phase2-spec.md §8）。

TestClient（alembic 経路）で各エンドポイントを叩き、
Pydantic 形・valuation_meta・is_delayed/as_of・0..1 単位を検証する。
quant は実際に呼ぶ（外部 API は叩かない）。
"""

from __future__ import annotations

import pytest

from app.db import repo

# テスト用の銘柄データ
STOCK_A = {
    "code": "72030",
    "company_name": "トヨタ自動車",
    "sector33_code": "3700",
    "sector17_code": "6",
    "market_code": "0111",
    "is_etf": 0,
    "updated_at": "2026-06-01T00:00:00+00:00",
}
STOCK_B = {
    "code": "67580",
    "company_name": "ソニーグループ",
    "sector33_code": "3600",
    "sector17_code": "7",
    "market_code": "0111",
    "is_etf": 0,
    "updated_at": "2026-06-01T00:00:00+00:00",
}


def _seed_daily_quotes(code: str, prices: list[float], start_date: str = "2025-01-02") -> None:
    """テスト用の日足データを seed する。prices は時系列順の終値リスト。"""
    from datetime import date, timedelta

    base = date.fromisoformat(start_date)
    rows = []
    for i, p in enumerate(prices):
        d = (base + timedelta(days=i)).isoformat()
        rows.append(
            {
                "code": code,
                "date": d,
                "open": p,
                "high": p * 1.01,
                "low": p * 0.99,
                "close": p,
                "volume": 1000.0,
                "adj_close": p,
            }
        )
    repo.upsert_daily_quotes(rows)


def _get_portfolio_id(client) -> int:
    """先頭ポートフォリオの id を返す。"""
    resp = client.get("/portfolios")
    assert resp.status_code == 200
    return resp.json()[0]["portfolio_id"]


# ---------------------------------------------------------------------------
# GET /portfolios
# ---------------------------------------------------------------------------


def test_portfolios_returns_default(client) -> None:
    """GET /portfolios は Default ポートフォリオを返す（seed 済み）。"""
    resp = client.get("/portfolios")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) >= 1
    assert body[0]["name"] == "Default"
    assert "portfolio_id" in body[0]


# ---------------------------------------------------------------------------
# GET /holdings（空）
# ---------------------------------------------------------------------------


def test_holdings_empty(client) -> None:
    """保有銘柄がない場合、holdings は空配列・valuation_meta は正しい形。"""
    pid = _get_portfolio_id(client)
    resp = client.get(f"/holdings?portfolio_id={pid}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["portfolio_id"] == pid
    assert body["holdings"] == []
    meta = body["valuation_meta"]
    assert "is_delayed" in meta
    assert meta["is_delayed"] is True
    assert meta["plan"] == "free"


# ---------------------------------------------------------------------------
# POST /transactions → TransactionResult
# ---------------------------------------------------------------------------


def test_post_transaction_creates_holding(client) -> None:
    """POST /transactions 後に TransactionResult.holdings に再計算後値が入る。"""
    repo.upsert_stocks([STOCK_A])
    pid = _get_portfolio_id(client)

    resp = client.post(
        "/transactions",
        json={
            "portfolio_id": pid,
            "code": "72030",
            "side": "buy",
            "shares": 100,
            "price": 1500,
            "traded_at": "2026-01-10",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert "transaction_id" in body
    assert isinstance(body["transaction_id"], int)

    h_resp = body["holdings"]
    assert h_resp["portfolio_id"] == pid
    holdings = h_resp["holdings"]
    assert len(holdings) == 1
    h = holdings[0]
    assert h["code"] == "72030"
    assert h["shares"] == 100.0
    assert h["avg_cost"] == 1500.0
    assert h["company_name"] == "トヨタ自動車"

    # valuation_meta の検証
    meta = h_resp["valuation_meta"]
    assert meta["is_delayed"] is True
    assert meta["plan"] == "free"


def test_post_transaction_with_market_value(client) -> None:
    """daily_quotes が seed されていれば market_value / unrealized_pnl / weight が計算される。"""
    repo.upsert_stocks([STOCK_A])
    _seed_daily_quotes("72030", [1400, 1500, 1600])
    pid = _get_portfolio_id(client)

    resp = client.post(
        "/transactions",
        json={
            "portfolio_id": pid,
            "code": "72030",
            "side": "buy",
            "shares": 100,
            "price": 1500,
            "traded_at": "2026-01-10",
        },
    )
    assert resp.status_code == 201
    h = resp.json()["holdings"]["holdings"][0]

    # last_close は最新日の close（1600）
    assert h["last_close"] == 1600.0
    # market_value = 100 * 1600 = 160000
    assert h["market_value"] == 160000.0
    # unrealized_pnl = 160000 - 100*1500 = 10000
    assert h["unrealized_pnl"] == 10000.0
    # weight（1銘柄なので 1.0）
    assert h["weight"] == 1.0

    # as_of は daily_quotes の最新日
    meta = resp.json()["holdings"]["valuation_meta"]
    assert meta["as_of"] is not None


def test_post_transaction_rolls_back_when_recalc_fails(client, monkeypatch) -> None:
    """holdings 再導出が失敗した場合、transactions だけを残さない（ADR-019・W2）。"""
    repo.upsert_stocks([STOCK_A])
    pid = _get_portfolio_id(client)

    def fail_recalc(*_args: object) -> None:
        raise RuntimeError("recalc failed")

    monkeypatch.setattr("app.routers.portfolio.recalc_holdings", fail_recalc)

    with pytest.raises(RuntimeError, match="recalc failed"):
        client.post(
            "/transactions",
            json={
                "portfolio_id": pid,
                "code": "72030",
                "side": "buy",
                "shares": 100,
                "price": 1500,
                "traded_at": "2026-01-10",
            },
        )

    from app.db.engine import get_engine

    with get_engine().connect() as conn:
        assert repo.list_transactions(conn, pid) == []
        assert repo.list_holdings(conn, pid) == []


# ---------------------------------------------------------------------------
# GET /transactions（履歴一覧・新しい順・company_name 付き）
# ---------------------------------------------------------------------------


def test_list_transactions_newest_first(client) -> None:
    """GET /transactions は取引を新しい順で company_name 付きで返す（spec P2-2・ADR-019）。"""
    repo.upsert_stocks([STOCK_A])
    pid = _get_portfolio_id(client)

    # 古い取引 → 新しい取引の順に投入（API は新しい順で返すべき）
    client.post(
        "/transactions",
        json={
            "portfolio_id": pid,
            "code": "72030",
            "side": "buy",
            "shares": 100,
            "price": 1000,
            "traded_at": "2026-01-10",
        },
    )
    client.post(
        "/transactions",
        json={
            "portfolio_id": pid,
            "code": "72030",
            "side": "buy",
            "shares": 50,
            "price": 1200,
            "traded_at": "2026-02-15",
        },
    )

    resp = client.get(f"/transactions?portfolio_id={pid}")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    # 新しい順（traded_at 降順）
    assert body[0]["traded_at"] == "2026-02-15"
    assert body[1]["traded_at"] == "2026-01-10"
    # company_name が付与される
    assert body[0]["company_name"] == "トヨタ自動車"
    # フィールド形の確認
    assert body[0]["code"] == "72030"
    assert body[0]["side"] == "buy"
    assert body[0]["shares"] == 50.0
    assert body[0]["price"] == 1200.0


def test_list_transactions_empty(client) -> None:
    """取引がない場合 GET /transactions は空配列を返す。"""
    pid = _get_portfolio_id(client)
    resp = client.get(f"/transactions?portfolio_id={pid}")
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# PUT /transactions/{id}（編集 → holdings 再導出）
# ---------------------------------------------------------------------------


def test_put_transaction_recalcs_holdings(client) -> None:
    """PUT で株数・単価を変更すると holdings の shares・avg_cost が再計算される。"""
    repo.upsert_stocks([STOCK_A])
    pid = _get_portfolio_id(client)

    create = client.post(
        "/transactions",
        json={
            "portfolio_id": pid,
            "code": "72030",
            "side": "buy",
            "shares": 100,
            "price": 1500,
            "traded_at": "2026-01-10",
        },
    )
    txn_id = create.json()["transaction_id"]

    # 株数 200・単価 1800 に編集
    resp = client.put(
        f"/transactions/{txn_id}",
        json={
            "portfolio_id": pid,
            "code": "72030",
            "side": "buy",
            "shares": 200,
            "price": 1800,
            "traded_at": "2026-01-10",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["transaction_id"] == txn_id

    holdings = body["holdings"]["holdings"]
    assert len(holdings) == 1
    assert holdings[0]["shares"] == 200.0
    assert holdings[0]["avg_cost"] == 1800.0


def test_put_transaction_recalcs_actual_portfolio_not_body(client) -> None:
    """#26: PUT の recalc は取引の実所属 portfolio で行う（body.portfolio_id が誤っても実所属側）。

    旧実装は body.portfolio_id で recalc したため、body に別（誤）portfolio_id を渡すと実所属側の
    holdings が取引と乖離した。実所属で recalc することを固定する。
    """
    repo.upsert_stocks([STOCK_A])
    pid = _get_portfolio_id(client)

    create = client.post(
        "/transactions",
        json={
            "portfolio_id": pid,
            "code": "72030",
            "side": "buy",
            "shares": 100,
            "price": 1500,
            "traded_at": "2026-01-10",
        },
    )
    txn_id = create.json()["transaction_id"]

    # 株数 50 に編集するが body.portfolio_id は実在しない 999（誤指定）を渡す。
    resp = client.put(
        f"/transactions/{txn_id}",
        json={
            "portfolio_id": 999,
            "code": "72030",
            "side": "buy",
            "shares": 50,
            "price": 1500,
            "traded_at": "2026-01-10",
        },
    )
    assert resp.status_code == 200

    # 実所属 pid の holdings が 50 に再計算されている（body の 999 側で recalc していない）。
    holdings = client.get(f"/holdings?portfolio_id={pid}").json()["holdings"]
    row = next(h for h in holdings if h["code"] == "72030")
    assert row["shares"] == 50.0


def test_put_transaction_404(client) -> None:
    """存在しない取引 id への PUT は 404。"""
    pid = _get_portfolio_id(client)
    resp = client.put(
        "/transactions/99999",
        json={
            "portfolio_id": pid,
            "code": "72030",
            "side": "buy",
            "shares": 100,
            "price": 1500,
            "traded_at": "2026-01-10",
        },
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /transactions/{id}（削除 → holdings 再導出）
# ---------------------------------------------------------------------------


def test_delete_transaction_removes_holding(client) -> None:
    """唯一の取引を DELETE すると holdings 行が消える（全売却相当）。"""
    repo.upsert_stocks([STOCK_A])
    pid = _get_portfolio_id(client)

    create = client.post(
        "/transactions",
        json={
            "portfolio_id": pid,
            "code": "72030",
            "side": "buy",
            "shares": 100,
            "price": 1500,
            "traded_at": "2026-01-10",
        },
    )
    txn_id = create.json()["transaction_id"]

    resp = client.delete(f"/transactions/{txn_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["transaction_id"] == txn_id
    # holdings が空に再導出される
    assert body["holdings"]["holdings"] == []

    # 取引一覧も空
    listed = client.get(f"/transactions?portfolio_id={pid}")
    assert listed.json() == []


def test_delete_transaction_partial_recalc(client) -> None:
    """複数取引の 1 件を DELETE すると残りから holdings が再導出される。"""
    repo.upsert_stocks([STOCK_A])
    pid = _get_portfolio_id(client)

    first = client.post(
        "/transactions",
        json={
            "portfolio_id": pid,
            "code": "72030",
            "side": "buy",
            "shares": 100,
            "price": 1000,
            "traded_at": "2026-01-10",
        },
    )
    client.post(
        "/transactions",
        json={
            "portfolio_id": pid,
            "code": "72030",
            "side": "buy",
            "shares": 100,
            "price": 2000,
            "traded_at": "2026-02-10",
        },
    )

    # 1 件目（100 株 @1000）を削除 → 残りは 100 株 @2000
    resp = client.delete(f"/transactions/{first.json()['transaction_id']}")
    assert resp.status_code == 200
    holdings = resp.json()["holdings"]["holdings"]
    assert len(holdings) == 1
    assert holdings[0]["shares"] == 100.0
    assert holdings[0]["avg_cost"] == 2000.0


def test_delete_transaction_404(client) -> None:
    """存在しない取引 id への DELETE は 404。"""
    resp = client.delete("/transactions/99999")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /holdings
# ---------------------------------------------------------------------------


def test_get_holdings_after_transactions(client) -> None:
    """POST /transactions 後に GET /holdings で最新 holdings が返る。"""
    repo.upsert_stocks([STOCK_A, STOCK_B])
    pid = _get_portfolio_id(client)

    client.post(
        "/transactions",
        json={
            "portfolio_id": pid,
            "code": "72030",
            "side": "buy",
            "shares": 100,
            "price": 1000,
            "traded_at": "2026-01-10",
        },
    )
    client.post(
        "/transactions",
        json={
            "portfolio_id": pid,
            "code": "67580",
            "side": "buy",
            "shares": 50,
            "price": 2000,
            "traded_at": "2026-01-10",
        },
    )

    resp = client.get(f"/holdings?portfolio_id={pid}")
    assert resp.status_code == 200
    holdings = resp.json()["holdings"]
    assert len(holdings) == 2
    codes = {h["code"] for h in holdings}
    assert codes == {"72030", "67580"}


# ---------------------------------------------------------------------------
# GET/PUT /cash
# ---------------------------------------------------------------------------


def test_cash_not_found(client) -> None:
    """cash 未登録で GET /cash は 404。"""
    resp = client.get("/cash")
    assert resp.status_code == 404


def test_put_cash_creates_and_updates(client) -> None:
    """PUT /cash で作成・更新できる。"""
    resp = client.put("/cash", json={"balance": 500000.0})
    assert resp.status_code == 200
    body = resp.json()
    assert body["balance"] == 500000.0
    assert "updated_at" in body

    # 更新
    resp2 = client.put("/cash", json={"balance": 300000.0})
    assert resp2.status_code == 200
    assert resp2.json()["balance"] == 300000.0

    # GET で確認
    resp3 = client.get("/cash")
    assert resp3.status_code == 200
    assert resp3.json()["balance"] == 300000.0


# ---------------------------------------------------------------------------
# /external-assets CRUD
# ---------------------------------------------------------------------------


def test_external_assets_crud(client) -> None:
    """外部資産の CRUD が正常に動作する。"""
    # 最初は空
    resp = client.get("/external-assets")
    assert resp.status_code == 200
    assert resp.json() == []

    # POST
    resp = client.post(
        "/external-assets",
        json={"name": "eMAXIS Slim オルカン", "category": "投信", "value": 1200000.0},
    )
    assert resp.status_code == 201
    body = resp.json()
    asset_id = body["id"]
    assert body["name"] == "eMAXIS Slim オルカン"
    assert body["value"] == 1200000.0

    # GET 一覧
    resp = client.get("/external-assets")
    assert len(resp.json()) == 1

    # PUT
    resp = client.put(
        f"/external-assets/{asset_id}",
        json={"name": "eMAXIS Slim オルカン", "value": 1300000.0},
    )
    assert resp.status_code == 200
    assert resp.json()["value"] == 1300000.0

    # DELETE
    resp = client.delete(f"/external-assets/{asset_id}")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # 削除後は空
    resp = client.get("/external-assets")
    assert resp.json() == []


def test_external_asset_delete_404(client) -> None:
    """存在しない外部資産を DELETE すると 404。"""
    resp = client.delete("/external-assets/99999")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /asset-overview
# ---------------------------------------------------------------------------


def test_asset_overview_empty(client) -> None:
    """保有・現金・外部資産が空の場合は total_value=0 で is_delayed=True。"""
    resp = client.get("/asset-overview")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_value"] == 0.0
    assert body["is_delayed"] is True
    assert body["plan"] == "free"
    assert "allocation" in body
    assert "deviations" in body
    assert "trend" in body
    assert "policy_targets" in body
    assert body["pnl"] == 0.0
    assert body["pnl_ratio"] is None  # 原価ゼロは損益率なし（backend 供給・ADR-014）


def test_asset_overview_with_stocks_and_cash(client) -> None:
    """株式・現金・外部資産がある場合、正しく集計される。"""
    repo.upsert_stocks([STOCK_A])
    _seed_daily_quotes("72030", [2000, 2100, 2200])
    pid = _get_portfolio_id(client)

    # 株を buy
    client.post(
        "/transactions",
        json={
            "portfolio_id": pid,
            "code": "72030",
            "side": "buy",
            "shares": 100,
            "price": 2000,
            "traded_at": "2026-01-10",
        },
    )
    # 現金を set
    client.put("/cash", json={"balance": 500000.0})
    # 外部資産を追加
    client.post(
        "/external-assets",
        json={"name": "オルカン", "category": "投信", "value": 300000.0},
    )

    resp = client.get("/asset-overview")
    assert resp.status_code == 200
    body = resp.json()

    # stock_value = 100 * 2200 = 220000
    assert body["stock_value"] == 220000.0
    assert body["cash_value"] == 500000.0
    assert body["external_value"] == 300000.0
    assert body["total_value"] == 1020000.0

    # allocation の weight の合計は 1.0（誤差許容）
    import pytest

    total_weight = sum(s["weight"] for s in body["allocation"])
    assert total_weight == pytest.approx(1.0, abs=1e-6)

    # 含み益 = 100*(2200-2000) = 20000。損益率は総資産ベース＝pnl/取得原価（ADR-014）。
    assert body["pnl"] == 20000.0
    assert body["pnl_ratio"] == pytest.approx(0.02)  # 20000 / (1020000 - 20000)

    # policy_targets の単位確認（0..1）
    targets = body["policy_targets"]
    assert 0 <= (targets.get("target_cash_ratio") or 0) <= 1
    assert 0 <= (targets.get("max_position_weight") or 0) <= 1


def test_asset_overview_with_policy_row(client) -> None:
    """policy 行が存在しても GET /asset-overview は 200（2026-06-12 の 500 回帰・ADR-013）。

    PUT /policy で行を作ると sector_caps が DB に JSON 文字列で入る。読み出しで dict に
    正規化しないと、truthy な文字列 '{}' が compute_deviations の `.items()` で
    AttributeError を起こし 500 になっていた（services/policy.get_policy のパース漏れ）。
    """
    # 実機で 500 を引き起こした操作と同じ入口（policy 行の作成）。
    res = client.put("/policy", json={"core": {"sector_caps": {}, "exclusions": []}})
    assert res.status_code == 200

    resp = client.get("/asset-overview")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["deviations"], list)
    assert "policy_targets" in body


# ---------------------------------------------------------------------------
# GET /portfolio/{id}/metrics
# ---------------------------------------------------------------------------


def test_portfolio_metrics_empty(client) -> None:
    """保有銘柄がない場合は指標が null でも 200 を返す。"""
    pid = _get_portfolio_id(client)
    resp = client.get(f"/portfolio/{pid}/metrics")
    assert resp.status_code == 200
    body = resp.json()
    assert body["portfolio_id"] == pid
    assert body["is_delayed"] is True
    # 保有なし → 指標は null
    assert body["annual_return"] is None
    assert body["sharpe"] is None
    assert body["correlation"]["codes"] == []


def test_portfolio_metrics_with_holdings(client) -> None:
    """2 銘柄 + 日足があれば相関・シャープが計算される。"""
    repo.upsert_stocks([STOCK_A, STOCK_B])
    # 252 日分の日足を seed（metrics 計算には最低 2 行必要）
    import random

    random.seed(42)
    prices_a = [1000.0]
    prices_b = [2000.0]
    for _ in range(100):
        prices_a.append(prices_a[-1] * (1 + random.uniform(-0.02, 0.02)))
        prices_b.append(prices_b[-1] * (1 + random.uniform(-0.02, 0.02)))

    _seed_daily_quotes("72030", prices_a)
    _seed_daily_quotes("67580", prices_b)

    pid = _get_portfolio_id(client)

    client.post(
        "/transactions",
        json={
            "portfolio_id": pid,
            "code": "72030",
            "side": "buy",
            "shares": 100,
            "price": 1000,
            "traded_at": "2026-01-10",
        },
    )
    client.post(
        "/transactions",
        json={
            "portfolio_id": pid,
            "code": "67580",
            "side": "buy",
            "shares": 50,
            "price": 2000,
            "traded_at": "2026-01-10",
        },
    )

    resp = client.get(f"/portfolio/{pid}/metrics")
    assert resp.status_code == 200
    body = resp.json()
    assert body["portfolio_id"] == pid
    assert body["is_delayed"] is True

    # 2 銘柄があれば correlation.codes は 2 要素
    corr = body["correlation"]
    assert len(corr["codes"]) == 2
    assert len(corr["matrix"]) == 2

    # deviations が返る（policy の DEFAULT_POLICY から）
    assert isinstance(body["deviations"], list)


def test_portfolio_metrics_with_policy_row(client) -> None:
    """policy 行（sector_caps 付き）が存在しても GET metrics は 200 で sector_cap 逸脱が出る。

    compute_portfolio_metrics は内部でも compute_deviations を呼ぶため、asset-overview と
    独立にこの経路でも JSON 文字列の sector_caps で落ちていた（ADR-013・500 回帰）。
    """
    repo.upsert_stocks([STOCK_A])
    _seed_daily_quotes("72030", [2000, 2100, 2200])
    pid = _get_portfolio_id(client)
    client.post(
        "/transactions",
        json={
            "portfolio_id": pid,
            "code": "72030",
            "side": "buy",
            "shares": 100,
            "price": 2000,
            "traded_at": "2026-01-10",
        },
    )
    res = client.put("/policy", json={"core": {"sector_caps": {"3700": 0.5}}})
    assert res.status_code == 200

    resp = client.get(f"/portfolio/{pid}/metrics")
    assert resp.status_code == 200
    body = resp.json()
    kinds = [d["kind"] for d in body["deviations"]]
    assert "sector_cap" in kinds


# ---------------------------------------------------------------------------
# POST /portfolio/{id}/optimize
# ---------------------------------------------------------------------------


def test_portfolio_optimize(client) -> None:
    """2 銘柄 + 日足があれば最適化が動く（infeasible でなければウェイトが返る）。"""
    repo.upsert_stocks([STOCK_A, STOCK_B])
    import random

    random.seed(0)
    prices_a = [1000.0]
    prices_b = [2000.0]
    for _ in range(100):
        prices_a.append(prices_a[-1] * (1 + random.uniform(-0.02, 0.02)))
        prices_b.append(prices_b[-1] * (1 + random.uniform(-0.02, 0.02)))

    _seed_daily_quotes("72030", prices_a)
    _seed_daily_quotes("67580", prices_b)

    pid = _get_portfolio_id(client)

    client.post(
        "/transactions",
        json={
            "portfolio_id": pid,
            "code": "72030",
            "side": "buy",
            "shares": 100,
            "price": 1000,
            "traded_at": "2026-01-10",
        },
    )
    client.post(
        "/transactions",
        json={
            "portfolio_id": pid,
            "code": "67580",
            "side": "buy",
            "shares": 50,
            "price": 2000,
            "traded_at": "2026-01-10",
        },
    )

    resp = client.post(f"/portfolio/{pid}/optimize")
    assert resp.status_code == 200
    body = resp.json()
    assert body["portfolio_id"] == pid
    assert body["is_delayed"] is True
    assert "infeasible" in body
    assert "weights" in body

    # infeasible でなければ weights に company_name が付く
    if not body["infeasible"]:
        for w in body["weights"]:
            assert "code" in w
            assert "target_weight" in w
            # target_weight は 0..1
            assert 0 <= w["target_weight"] <= 1


def test_portfolio_optimize_with_policy_row_excludes(client) -> None:
    """policy 行（exclusions 付き）が存在しても POST optimize は 200 で除外が効く。

    exclusions が JSON 文字列のままだと `list('["67580"]')` が文字単位に分解されて除外が
    静かに無効化し、sector_caps は `dict(文字列)` で ValueError になっていた（ADR-013 回帰）。
    """
    repo.upsert_stocks([STOCK_A, STOCK_B])
    import random

    random.seed(1)
    prices_a = [1000.0]
    prices_b = [2000.0]
    for _ in range(100):
        prices_a.append(prices_a[-1] * (1 + random.uniform(-0.02, 0.02)))
        prices_b.append(prices_b[-1] * (1 + random.uniform(-0.02, 0.02)))
    _seed_daily_quotes("72030", prices_a)
    _seed_daily_quotes("67580", prices_b)

    pid = _get_portfolio_id(client)
    for code, shares, price in (("72030", 100, 1000), ("67580", 50, 2000)):
        client.post(
            "/transactions",
            json={
                "portfolio_id": pid,
                "code": code,
                "side": "buy",
                "shares": shares,
                "price": price,
                "traded_at": "2026-01-10",
            },
        )
    res = client.put(
        "/policy", json={"core": {"sector_caps": {"3700": 0.5}, "exclusions": ["67580"]}}
    )
    assert res.status_code == 200

    resp = client.post(f"/portfolio/{pid}/optimize")
    assert resp.status_code == 200
    body = resp.json()
    # 除外銘柄はウェイトに現れない（infeasible でも weights=[] なので同じ assert で通る）。
    assert all(w["code"] != "67580" for w in body["weights"])


# ---------------------------------------------------------------------------
# GET /portfolio/{id}/backtest（過去シミュレーション・spec §4.4）
# ---------------------------------------------------------------------------


def _seed_index_quotes(symbol: str, prices: list[float], start_date: str = "2025-01-02") -> None:
    """テスト用の指数水準を seed する（backtest のベンチ用）。"""
    from datetime import date, timedelta

    base = date.fromisoformat(start_date)
    rows = [
        {"symbol": symbol, "date": (base + timedelta(days=i)).isoformat(), "close": p}
        for i, p in enumerate(prices)
    ]
    repo.upsert_index_quotes(rows)


def test_portfolio_backtest_empty(client) -> None:
    """保有なし／ベンチなしでも 200・空 leg（as_of=None / curve=[]）を返す。"""
    pid = _get_portfolio_id(client)
    resp = client.get(f"/portfolio/{pid}/backtest")
    assert resp.status_code == 200
    body = resp.json()
    assert body["portfolio_id"] == pid
    assert body["is_delayed"] is True
    assert body["as_of"] is None
    assert body["portfolio"]["curve"] == []
    assert body["benchmark"]["curve"] == []
    assert body["excess_return"] == 0.0


def test_portfolio_backtest_404(client) -> None:
    """存在しないポートフォリオは 404。"""
    resp = client.get("/portfolio/99999/backtest")
    assert resp.status_code == 404


def test_portfolio_backtest_with_holdings(client) -> None:
    """保有＋日足＋ベンチ（^TPX）があれば 2 本のカーブと超過リターンが返る。"""
    repo.upsert_stocks([STOCK_A])
    # ポートは右肩上がり、ベンチは横ばいにして excess_return > 0 を作る
    prices = [1000.0 * (1.0 + 0.01 * i) for i in range(30)]
    bench = [1500.0] * 30
    _seed_daily_quotes("72030", prices)
    _seed_index_quotes("^TPX", bench)

    pid = _get_portfolio_id(client)
    client.post(
        "/transactions",
        json={
            "portfolio_id": pid,
            "code": "72030",
            "side": "buy",
            "shares": 100,
            "price": 1000,
            "traded_at": "2026-01-10",
        },
    )

    resp = client.get(f"/portfolio/{pid}/backtest")
    assert resp.status_code == 200
    body = resp.json()
    assert body["as_of"] is not None
    # ポートのカーブは 2 点以上、1 始まりの倍率
    assert len(body["portfolio"]["curve"]) >= 2
    assert body["portfolio"]["curve"][0]["value"] > 0
    # ポートは上昇・ベンチは横ばい → 累積/年率/超過がプラス
    assert body["portfolio"]["cumulative_return"] > 0
    assert body["benchmark"]["cumulative_return"] == pytest.approx(0.0, abs=1e-9)
    assert body["excess_return"] > 0
