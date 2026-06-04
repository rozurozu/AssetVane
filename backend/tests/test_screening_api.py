"""スクリーニング API（/stocks/screen・/screening-filters）の TestClient テスト（ADR-031）。"""

from __future__ import annotations

from app.db import repo


def _stock(code: str, name: str, sector: str, is_etf: int = 0) -> dict:
    return {
        "code": code,
        "company_name": name,
        "sector33_code": sector,
        "sector17_code": "6",
        "market_code": "0111",
        "is_etf": is_etf,
        "updated_at": "2026-06-04T00:00:00+00:00",
    }


def _snap(code: str, per, pbr, mcap, dy) -> dict:
    return {
        "code": code,
        "as_of_date": "2026-06-03",
        "close": 1000.0,
        "eps": 100.0,
        "bps": 500.0,
        "dividend_per_share": 30.0,
        "shares_net": (mcap / 1000.0) if mcap else None,
        "per": per,
        "pbr": pbr,
        "market_cap": mcap,
        "dividend_yield": dy,
        "fin_disclosed_date": "2025-05-08",
        "updated_at": "2026-06-04T00:00:00+00:00",
    }


def _seed() -> None:
    repo.upsert_stocks(
        [
            _stock("1000", "安いA", "3700"),
            _stock("1001", "高いB", "3700"),
            _stock("2000", "別業種C", "5250"),
            _stock("9999", "ETF", "3700", is_etf=1),
        ]
    )
    repo.upsert_valuation_snapshots(
        [
            _snap("1000", per=8.0, pbr=0.8, mcap=500e8, dy=0.04),
            _snap("1001", per=25.0, pbr=3.0, mcap=2000e8, dy=0.01),
            _snap("2000", per=12.0, pbr=1.5, mcap=8000e8, dy=0.02),
            _snap("9999", per=15.0, pbr=1.0, mcap=300e8, dy=0.0),
        ]
    )


def test_screen_absolute_range(client) -> None:
    _seed()
    rows = client.get("/stocks/screen", params={"per_max": 13, "dividend_yield_min": 0.015}).json()
    assert {r["code"] for r in rows} == {"1000", "2000"}
    # 出力に指標・ランク列がある
    a = next(r for r in rows if r["code"] == "1000")
    assert a["company_name"] == "安いA" and a["per"] == 8.0
    assert "per_sector_pctile" in a and "market_cap_rank" in a


def test_screen_route_not_eaten_by_code(client) -> None:
    # /stocks/screen が /stocks/{code} に食われていない（code="screen" の 404 にならない）
    assert client.get("/stocks/screen").status_code == 200


def test_screen_rank_and_sector(client) -> None:
    _seed()
    top2 = client.get("/stocks/screen", params={"market_cap_rank_max": 2}).json()
    assert {r["code"] for r in top2} == {"2000", "1001"}
    jp = client.get("/stocks/screen", params={"sector33_code": "3700", "exclude_etf": True}).json()
    assert {r["code"] for r in jp} == {"1000", "1001"}


def test_screening_filters_crud_via_http(client) -> None:
    # 作成
    created = client.post(
        "/screening-filters",
        json={"name": "割安高配当", "criteria": {"per_max": 15, "dividend_yield_min": 0.03}},
    ).json()
    fid = created["id"]
    assert created["name"] == "割安高配当"
    assert created["criteria"]["per_max"] == 15

    # 一覧
    all_f = client.get("/screening-filters").json()
    assert len(all_f) == 1

    # 更新
    upd = client.put(
        f"/screening-filters/{fid}",
        json={"name": "改名", "criteria": {"per_max": 10}},
    ).json()
    assert upd["name"] == "改名" and upd["criteria"] == {"per_max": 10}

    # 削除
    assert client.delete(f"/screening-filters/{fid}").json() == {"ok": True}
    assert client.get("/screening-filters").json() == []
    # 未存在の更新/削除は 404
    assert (
        client.put("/screening-filters/999", json={"name": "x", "criteria": {}}).status_code == 404
    )
    assert client.delete("/screening-filters/999").status_code == 404
