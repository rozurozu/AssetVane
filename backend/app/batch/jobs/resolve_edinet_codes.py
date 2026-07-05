"""sec_code→edinet_code 全件スイープジョブ（JP・ADR-083）。

edinetdb.jp の `/companies` を全ページ舐めて {sec_code: edinet_code} の対応表を作り、
`stocks.edinet_code` に一括反映する（`bulk_set_stock_edinet_codes`）。全銘柄ネットキャッシュ
バックフィル（calc_receivables_inventory の全ユニバース化・ADR-083）の前提＝各銘柄を 1 件ずつ
`resolve_edinet_code` で解決するとレート予算（free 日100/月600）を溶かすため、一覧スイープで
一括解決する（全上場 約3,834 社が per_page=100 で 約39 リクエスト）。

**cadence**: 新規上場は稀なので月次で十分（`edinetdb_sweep_interval_days`＝30・fetch_meta で追跡）。
NIGHTLY_JOBS に毎晩入れても実際にスイープするのは月 1 回。**full_backfill=True（初回ボタン）は
cadence を無視して必ずスイープ**する（初回に edinet_code を埋めるため）。

未設定（edinetdb_config 未登録）は静かに skip（ok=True・ADR-064）。停止は最内ループで見る
（should_stop・ADR-070）。ジョブ全体の例外は JobResult(ok=False) で runner に返す（ADR-018）。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from app.batch import state
from app.batch.runner import JobResult
from app.config import settings
from app.db import repo
from app.db.engine import get_engine
from app.services.edinetdb_config import build_edinetdb_adapter, resolve_edinetdb_config

logger = logging.getLogger(__name__)

_NAME = "resolve_edinet_codes"
_META_KEY = "edinet_code_sweep"
_PER_PAGE = 100  # /companies の 1 ページ件数（大きくして総ページ数＝リクエスト数を減らす）


def _recently_swept(conn, today: str) -> bool:
    """前回スイープから edinetdb_sweep_interval_days 以内か（月次 cadence・fetch_meta 追跡）。"""
    meta = repo.get_fetch_meta(conn, _META_KEY)
    last = (meta or {}).get("last_fetched_date")
    if not last:
        return False
    try:
        gap = (date.fromisoformat(today) - date.fromisoformat(last)).days
    except ValueError:
        return False
    return gap < settings.edinetdb_sweep_interval_days


def _sweep_companies(adapter: Any) -> dict[str, str]:
    """/companies を全ページ舐めて {sec_code: edinet_code} を返す（sec/edinet 欠落行は除く）。

    ページングは meta.pagination.total_pages に従う。停止要求を各ページ先頭で見る（while ループは
    stop_aware 非対象＝先頭で should_stop・batch-pattern）。
    """
    mapping: dict[str, str] = {}
    page = 1
    while True:
        if state.should_stop():
            break
        payload = adapter.list_companies(page=page, per_page=_PER_PAGE)
        data = payload.get("data") or []
        for row in data:
            sec = row.get("sec_code")
            ecode = row.get("edinet_code")
            if sec and ecode:
                mapping[str(sec)] = str(ecode)
        pagination = (payload.get("meta") or {}).get("pagination") or {}
        total_pages = pagination.get("total_pages") or 0
        if not data or page >= total_pages:
            break
        page += 1
    return mapping


def run(full_backfill: bool = False) -> JobResult:
    """会社一覧から sec_code→edinet_code を全件スイープして stocks に焼く（ADR-083）。"""
    try:
        today = date.today().isoformat()
        with get_engine().connect() as conn:
            if resolve_edinetdb_config(conn) is None:
                return JobResult(
                    name=_NAME, ok=True, rows=0, detail="EDINET DB 未設定のため skip（ADR-064）"
                )
            if not full_backfill and _recently_swept(conn, today):
                return JobResult(
                    name=_NAME, ok=True, rows=0, detail="月次 cadence 内のため skip（ADR-083）"
                )
            adapter = build_edinetdb_adapter(conn)

        mapping = _sweep_companies(adapter)
        with get_engine().begin() as conn:
            updated = repo.bulk_set_stock_edinet_codes(conn, mapping)
        repo.upsert_fetch_meta(_META_KEY, today)
        return JobResult(
            name=_NAME,
            ok=True,
            rows=updated,
            detail=f"edinet_code {updated} 件解決（{len(mapping)} 社取得・ADR-083）",
        )
    except Exception as exc:  # noqa: BLE001 — ジョブ境界で握り runner に返す（ADR-018）
        logger.exception("resolve_edinet_codes が失敗")
        return JobResult(name=_NAME, ok=False, rows=0, detail=f"失敗: {exc}")
