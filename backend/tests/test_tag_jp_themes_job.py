"""tag_jp_themes ジョブの巡回・カーソル前進・部分失敗・prune 同居・bump-only を検証する（段階B）。

tag_us_themes（段階A）と対称。違いは信号源の出所（JP は investigate_stock のドシエ要約＝
company_descriptions(market='JP', source='dossier')）・選定 JOIN 先（stocks）・カーソルキー
（'jp_themes:<code>'）・prune の market='JP' 限定、そして JP 固有の **bump-only 最適化**
（説明未変化なら LLM を呼ばず last_seen_at だけ bump・ADR-050 段階B）。

担保すること:
- 種テーマ（SEED_THEMES）が themes 目録へ冪等に入る（コールドスタート・ADR-050）。
- 選定→成功で fetch_meta['jp_themes:<code>'] が ISO datetime で前進。
- 1 銘柄の失敗で ok=False になりつつ後続を止めない（ADR-018）。失敗銘柄は last_attempt_ok=0。
- 末尾の時間窓 prune が JP の stale タグだけ枯らす（US は触らない・market 安全弁）。
- 選定 0 件（company_descriptions の JP 行が空）は ok=True・rows=0 で静かに返し prune もしない。
- **bump-only**: 説明未変化（fetched_at <= 前回カーソル）の銘柄は LLM を呼ばず bump のみ。

tag_stock_themes は monkeypatch（LLM/ネットに出ない・testing-strategy）。
"""

from __future__ import annotations

from typing import Any

from app.batch.jobs import tag_jp_themes
from app.config import settings
from app.db import repo
from app.db.engine import get_engine
from app.db.schema import fetch_meta, stocks
from app.reference.theme_seeds import SEED_THEMES

T1 = "2026-06-01T00:00:00+00:00"
T2 = "2026-06-05T00:00:00+00:00"


def _seed_jp_stock(code: str, *, is_etf: int = 0) -> None:
    """選定 JOIN の前提となる stocks 行を入れる（is_etf=0 が巡回対象）。"""
    with get_engine().begin() as conn:
        conn.execute(stocks.insert().values(code=code, company_name=f"{code}社", is_etf=is_etf))


def _seed_description(code: str, text: str = "ウィジェット設計。", fetched_at: str = T1) -> None:
    """選定の起点となる company_descriptions 行を入れる（market='JP', source='dossier'）。"""
    repo.upsert_company_description(
        {
            "market": "JP",
            "code": code,
            "source": "dossier",
            "description_text": text,
            "disclosed_date": None,
            "doc_id": None,
            "fetched_at": fetched_at,
        }
    )


def _make_fake_tagger(calls: list[str], *, fail_codes: set[str] | None = None):
    """tag_stock_themes の fake（async・呼び出し記録＋指定銘柄で例外）。"""
    fail = fail_codes or set()

    async def _fake(conn, *, market: str, code: str) -> dict[str, Any]:  # noqa: ANN001 — テスト fake
        calls.append(code)
        if code in fail:
            raise RuntimeError("LLM down")
        return {"code": code, "themes": ["生成AI"], "n_new_themes": 1}

    return _fake


def test_seed_themes_and_cursor_advance(temp_db, monkeypatch) -> None:
    """種テーマが目録に入り、成功銘柄の fetch_meta が ISO datetime で前進する（ADR-050 段階B）。"""
    _seed_jp_stock("11110")
    _seed_jp_stock("22220")
    _seed_description("11110")
    _seed_description("22220")

    calls: list[str] = []
    monkeypatch.setattr(tag_jp_themes, "tag_stock_themes", _make_fake_tagger(calls))
    result = tag_jp_themes.run()

    assert result.ok is True
    assert result.rows == 2
    assert sorted(calls) == ["11110", "22220"]  # 未タグ＝① なので両方 LLM

    with get_engine().connect() as conn:
        names = repo.list_theme_names(conn)
        meta = repo.get_fetch_meta(conn, "jp_themes:11110")
    assert set(SEED_THEMES) <= set(names)
    assert meta is not None
    assert "T" in str(meta["last_fetched_date"])  # ISO datetime（時刻まで）
    assert "新テーマ 2 件" in result.detail
    assert "prune 0 行" in result.detail


def test_nightly_cap_limits_targets(temp_db, monkeypatch) -> None:
    """夜あたり天井（theme_tagging_jp_nightly_max）で処理本数が頭打ちになる（ADR-033）。"""
    for code in ("11110", "22220", "33330"):
        _seed_jp_stock(code)
        _seed_description(code)
    monkeypatch.setattr(settings, "theme_tagging_jp_nightly_max", 2)

    calls: list[str] = []
    monkeypatch.setattr(tag_jp_themes, "tag_stock_themes", _make_fake_tagger(calls))
    result = tag_jp_themes.run()

    assert result.ok is True
    assert result.rows == 2
    assert len(calls) == 2


def test_partial_failure_keeps_going(temp_db, monkeypatch) -> None:
    """1 銘柄の失敗で ok=False になりつつ後続を止めない（ADR-018）。失敗は last_attempt_ok=0。"""
    for code in ("11110", "20000", "33330"):
        _seed_jp_stock(code)
        _seed_description(code)

    calls: list[str] = []
    monkeypatch.setattr(
        tag_jp_themes, "tag_stock_themes", _make_fake_tagger(calls, fail_codes={"20000"})
    )
    result = tag_jp_themes.run()

    assert result.ok is False  # 失敗 1 件あり
    assert result.rows == 2  # 成功は 2 銘柄
    assert sorted(calls) == ["11110", "20000", "33330"]  # 後続を止めていない
    assert "20000" in result.detail

    with get_engine().connect() as conn:
        bad_meta = repo.get_fetch_meta(conn, "jp_themes:20000")
        ok_meta = repo.get_fetch_meta(conn, "jp_themes:11110")
    assert bad_meta is not None and bad_meta["last_attempt_ok"] == 0  # 失敗を記録
    assert bad_meta["last_fetched_date"] is None  # 再開点は潰さない
    assert ok_meta is not None and ok_meta["last_fetched_date"]  # 成功銘柄は前進


def test_bump_only_skips_llm_for_unchanged(temp_db, monkeypatch) -> None:
    """説明未変化（fetched_at <= 前回カーソル）の銘柄は LLM を呼ばず bump のみ（段階B）。"""
    _seed_jp_stock("11110")
    _seed_description("11110", fetched_at=T1)
    # 既タグ済み（カーソルが説明 fetched_at より新しい）＝説明未変化として bump-only パスに乗る。
    with get_engine().begin() as conn:
        conn.execute(
            fetch_meta.insert().values(
                source="jp_themes:11110",
                last_fetched_date=T2,  # T2 > T1（説明 fetched_at）→ 未変化
                updated_at=T2,
                last_attempt_ok=1,
            )
        )
    repo.insert_themes_if_absent(["AI需要"], T1)
    repo.upsert_stock_themes(
        [
            {
                "market": "JP",
                "code": "11110",
                "theme_name": "AI需要",
                "first_assigned_at": T1,
                "last_seen_at": T1,
            }
        ]
    )

    calls: list[str] = []
    monkeypatch.setattr(tag_jp_themes, "tag_stock_themes", _make_fake_tagger(calls))
    result = tag_jp_themes.run()

    assert result.ok is True
    assert result.rows == 1
    assert calls == []  # LLM は呼ばれない（bump-only）
    assert "bump のみ 1 件" in result.detail

    with get_engine().connect() as conn:
        rows = repo.get_stock_themes(conn, "JP", "11110")
        meta = repo.get_fetch_meta(conn, "jp_themes:11110")
    assert rows[0]["last_seen_at"] != T1  # bump された（prune 回避）
    assert rows[0]["first_assigned_at"] == T1  # first は不変
    assert meta is not None
    assert str(meta["last_fetched_date"]) > T2  # カーソルも前進


def test_changed_description_retags_with_llm(temp_db, monkeypatch) -> None:
    """説明が前回タグ以降に変化した銘柄は LLM 再分類に乗る（bump-only に落ちない・ADR-050 ②）。"""
    _seed_jp_stock("11110")
    _seed_description("11110", fetched_at=T2)  # 説明 fetched_at が新しい
    with get_engine().begin() as conn:
        conn.execute(
            fetch_meta.insert().values(
                source="jp_themes:11110",
                last_fetched_date=T1,  # T2 > T1 → 説明変化
                updated_at=T1,
                last_attempt_ok=1,
            )
        )

    calls: list[str] = []
    monkeypatch.setattr(tag_jp_themes, "tag_stock_themes", _make_fake_tagger(calls))
    result = tag_jp_themes.run()

    assert result.ok is True
    assert calls == ["11110"]  # 変化 → LLM 再タグ
    assert "bump のみ 0 件" in result.detail


def test_prune_runs_with_tagger_and_market_scope(temp_db, monkeypatch) -> None:
    """末尾 prune が JP の stale タグだけ枯らし US は触らない（ADR-050・market 安全弁）。"""
    _seed_jp_stock("11110")
    _seed_description("11110")
    repo.insert_themes_if_absent(["旧テーマ", "新テーマ"], "2020-01-01T00:00:00+00:00")
    repo.upsert_stock_themes(
        [
            {
                "market": "JP",
                "code": "99999",
                "theme_name": "旧テーマ",
                "first_assigned_at": "2020-01-01T00:00:00+00:00",
                "last_seen_at": "2020-01-01T00:00:00+00:00",
            },
            {
                "market": "JP",
                "code": "11110",
                "theme_name": "新テーマ",
                "first_assigned_at": "2099-01-01T00:00:00+00:00",
                "last_seen_at": "2099-01-01T00:00:00+00:00",
            },
            {
                "market": "US",
                "code": "AAPL",
                "theme_name": "旧テーマ",
                "first_assigned_at": "2020-01-01T00:00:00+00:00",
                "last_seen_at": "2020-01-01T00:00:00+00:00",
            },
        ]
    )

    calls: list[str] = []
    monkeypatch.setattr(tag_jp_themes, "tag_stock_themes", _make_fake_tagger(calls))
    result = tag_jp_themes.run()

    assert result.ok is True
    assert "prune 1 行" in result.detail  # stale な JP タグ 1 行だけ枯れた

    with get_engine().connect() as conn:
        jp_stale = repo.get_stock_themes(conn, "JP", "99999")
        jp_fresh = repo.get_stock_themes(conn, "JP", "11110")
        us_stale = repo.get_stock_themes(conn, "US", "AAPL")
    assert jp_stale == []  # 時間窓を超えた JP タグは枯れる
    assert len(jp_fresh) >= 1  # 新鮮な JP タグは残る
    assert len(us_stale) == 1  # US は段階 A の領分＝触らない（market 安全弁）


def test_empty_selection_quiet_success(temp_db, monkeypatch) -> None:
    """選定 0 件（JP company_descriptions が空）は ok=True・rows=0 で静かに返し prune しない。"""
    repo.insert_themes_if_absent(["旧テーマ"], "2020-01-01T00:00:00+00:00")
    repo.upsert_stock_themes(
        [
            {
                "market": "JP",
                "code": "99999",
                "theme_name": "旧テーマ",
                "first_assigned_at": "2020-01-01T00:00:00+00:00",
                "last_seen_at": "2020-01-01T00:00:00+00:00",
            }
        ]
    )

    calls: list[str] = []
    monkeypatch.setattr(tag_jp_themes, "tag_stock_themes", _make_fake_tagger(calls))
    result = tag_jp_themes.run()

    assert result.ok is True
    assert result.rows == 0
    assert calls == []
    assert "巡回対象なし" in result.detail
    with get_engine().connect() as conn:
        remaining = repo.get_stock_themes(conn, "JP", "99999")
    assert len(remaining) == 1  # prune は走っていない
