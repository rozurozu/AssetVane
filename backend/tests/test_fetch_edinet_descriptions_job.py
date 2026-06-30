"""fetch_edinet_descriptions の crawl core 検証（テーマタグ段階C・ADR-056/033/018）。

担保: docTypeCode=120 かつユニバース内だけ取込／事前 skip（dossier・既存 edinet が最新）／
要約→upsert（source='edinet'）／提出日完了でカーソル前進／cap 到達で現提出日はカーソル据え置き／
冪等再走（事前 skip で撃ち直さない）。fake adapter ＋ fake summarize でネット・実 LLM に出ない。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.batch.jobs import fetch_edinet_descriptions as job
from app.db import repo
from app.db.engine import get_engine

_CRAWL_SOURCE = "edinet:crawl"


def _doc(
    doc_id: str, sec_code: str, *, doc_type: str = "120", period_end: str = "2025-03-31"
) -> dict[str, Any]:
    return {
        "doc_id": doc_id,
        "sec_code": sec_code,
        "doc_type_code": doc_type,
        "filer_name": f"会社{sec_code}",
        "period_end": period_end,
        "submit_datetime": "2025-06-25 09:00",
        "csv_flag": "1",
    }


class _FakeEdinet:
    """list_documents / fetch_business_description を持つ fake（実 HTTP に出ない）。"""

    def __init__(
        self, docs_by_date: dict[str, list[dict]], text_by_doc: dict[str, str | None]
    ) -> None:
        self._docs_by_date = docs_by_date
        self._text_by_doc = text_by_doc
        self.fetched: list[str] = []

    def list_documents(self, d: str) -> list[dict]:
        return self._docs_by_date.get(d, [])

    def fetch_business_description(self, doc_id: str) -> dict | None:
        self.fetched.append(doc_id)
        text = self._text_by_doc.get(doc_id)
        return {"doc_id": doc_id, "text": text} if text else None


def _fake_summarize(text: str) -> str:
    """要約の代わりに決定的な短い文字列を返す（実 LLM を避ける）。"""
    return f"要約:{text[:8]}"


def _seed_universe(*codes: str) -> None:
    repo.upsert_stocks(
        [{"code": c, "company_name": f"会社{c}", "is_etf": 0, "updated_at": "t"} for c in codes]
    )


def _cursor() -> str | None:
    with get_engine().connect() as conn:
        meta = repo.get_fetch_meta(conn, _CRAWL_SOURCE)
    return (meta or {}).get("last_fetched_date")


def _desc(code: str) -> dict[str, Any] | None:
    with get_engine().connect() as conn:
        return repo.get_company_description(conn, "JP", code)


def _latest_restatement(code: str) -> str | None:
    with get_engine().connect() as conn:
        return repo.get_latest_restatement_date(conn, code)


def test_crawl_takes_only_120_in_universe(temp_db) -> None:
    """docTypeCode=120 かつユニバース内だけ取り込み、カーソルを完了日へ前進する。"""
    _seed_universe("72030", "67580")
    adapter = _FakeEdinet(
        docs_by_date={
            "2025-06-25": [
                _doc("S1", "72030"),  # 取込対象
                _doc("S2", "99990"),  # ユニバース外 → skip
                _doc("S3", "67580", doc_type="140"),  # 有報でない → skip
            ]
        },
        text_by_doc={"S1": "産業用ロボットを製造する"},
    )
    result = job.crawl(
        start_date=date(2025, 6, 25),
        end_date=date(2025, 6, 25),
        cap=None,
        adapter=adapter,
        summarize_fn=_fake_summarize,
        log=lambda _m: None,
    )
    assert result["n_summarized"] == 1
    assert adapter.fetched == ["S1"]  # 対象外は fetch すらしない
    row = _desc("72030")
    assert row and row["source"] == "edinet" and row["disclosed_date"] == "2025-03-31"
    assert row["description_text"] == _fake_summarize("産業用ロボットを製造する")
    assert _desc("67580") is None
    assert _cursor() == "2025-06-25"


def test_crawl_pre_skips_dossier(temp_db) -> None:
    """既存 source='dossier' は要約 LLM を撃つ前に skip（dossier を上書きしない・コスト節約）。"""
    _seed_universe("72030")
    with get_engine().begin() as conn:
        repo.upsert_company_description_tx(
            conn, market="JP", code="72030", source="dossier", description_text="調査済み"
        )
    adapter = _FakeEdinet({"2025-06-25": [_doc("S1", "72030")]}, {"S1": "EDINET 本文"})
    result = job.crawl(
        start_date=date(2025, 6, 25),
        end_date=date(2025, 6, 25),
        cap=None,
        adapter=adapter,
        summarize_fn=_fake_summarize,
        log=lambda _m: None,
    )
    assert result["n_skip_dossier"] == 1
    assert adapter.fetched == []  # 事前 skip で fetch しない
    row = _desc("72030")
    assert row and row["source"] == "dossier"


def test_crawl_pre_skips_existing_edinet_not_newer(temp_db) -> None:
    """既存 edinet の disclosed_date が今回 period_end 以上なら skip（最新を持つ・冪等）。"""
    _seed_universe("72030")
    repo.upsert_company_description_edinet(
        {
            "market": "JP",
            "code": "72030",
            "source": "edinet",
            "description_text": "既存",
            "disclosed_date": "2025-03-31",
            "doc_id": "OLD",
            "fetched_at": "2026-06-01T00:00:00+00:00",
        }
    )
    adapter = _FakeEdinet(
        {"2025-06-25": [_doc("S1", "72030", period_end="2025-03-31")]}, {"S1": "新"}
    )
    result = job.crawl(
        start_date=date(2025, 6, 25),
        end_date=date(2025, 6, 25),
        cap=None,
        adapter=adapter,
        summarize_fn=_fake_summarize,
        log=lambda _m: None,
    )
    assert result["n_skip_existing"] == 1
    assert adapter.fetched == []


def test_crawl_cap_holds_cursor(temp_db) -> None:
    """cap 到達で現提出日はカーソルを進めない（次回その日から再開・事前 skip で重複撃たず）。"""
    _seed_universe("72030", "67580")
    adapter = _FakeEdinet(
        {"2025-06-25": [_doc("S1", "72030"), _doc("S2", "67580")]},
        {"S1": "本文A", "S2": "本文B"},
    )
    result = job.crawl(
        start_date=date(2025, 6, 25),
        end_date=date(2025, 6, 25),
        cap=1,
        adapter=adapter,
        summarize_fn=_fake_summarize,
        log=lambda _m: None,
    )
    assert result["n_summarized"] == 1
    assert result["cap_reached"] is True
    # 提出日は未完なのでカーソルは進まない（None のまま）。
    assert _cursor() is None
    assert result["last_cursor"] is None


def test_crawl_is_idempotent_on_rerun(temp_db) -> None:
    """同じ提出日を 2 回クロールしても 2 回目は事前 skip で要約を撃ち直さない（冪等）。"""
    _seed_universe("72030")
    adapter = _FakeEdinet({"2025-06-25": [_doc("S1", "72030")]}, {"S1": "本文"})
    common = {
        "start_date": date(2025, 6, 25),
        "end_date": date(2025, 6, 25),
        "adapter": adapter,
        "summarize_fn": _fake_summarize,
        "log": lambda _m: None,
    }
    job.crawl(cap=None, **common)
    adapter.fetched.clear()
    second = job.crawl(cap=None, **common)
    assert second["n_summarized"] == 0
    assert second["n_skip_existing"] == 1
    assert adapter.fetched == []


def test_crawl_records_130_restatement(temp_db) -> None:
    """docTypeCode=130（訂正有報）は本文を取らず出現だけ記録し、120 と独立に拾う（B-2）。"""
    _seed_universe("72030", "67580")
    adapter = _FakeEdinet(
        docs_by_date={
            "2025-06-25": [
                _doc("S1", "72030"),  # 120: 通常の取込
                _doc("R1", "67580", doc_type="130"),  # 130: 訂正 → 記録のみ
                _doc("R2", "99990", doc_type="130"),  # ユニバース外 → skip
            ]
        },
        text_by_doc={"S1": "産業用ロボットを製造する"},
    )
    result = job.crawl(
        start_date=date(2025, 6, 25),
        end_date=date(2025, 6, 25),
        cap=None,
        adapter=adapter,
        summarize_fn=_fake_summarize,
        log=lambda _m: None,
    )
    assert result["n_restatements"] == 1
    assert result["n_summarized"] == 1
    assert adapter.fetched == ["S1"]  # 訂正は本文を取らない（一覧の事実だけ）
    assert _latest_restatement("67580") == "2025-06-25"  # 提出日＝クロール日
    assert _latest_restatement("99990") is None  # ユニバース外は記録しない
    assert _latest_restatement("72030") is None  # 120 のみの銘柄は訂正なし


def test_crawl_restatement_is_idempotent(temp_db) -> None:
    """同じ訂正書類（doc_id）を 2 回クロールしても二重記録しない（doc_id 冪等・B-2）。"""
    _seed_universe("67580")
    adapter = _FakeEdinet({"2025-06-25": [_doc("R1", "67580", doc_type="130")]}, {})
    common = {
        "start_date": date(2025, 6, 25),
        "end_date": date(2025, 6, 25),
        "adapter": adapter,
        "summarize_fn": _fake_summarize,
        "log": lambda _m: None,
    }
    first = job.crawl(cap=None, **common)
    second = job.crawl(cap=None, **common)
    assert first["n_restatements"] == 1
    assert second["n_restatements"] == 0  # 2 回目は doc_id 冪等で新規ゼロ
    assert _latest_restatement("67580") == "2025-06-25"
