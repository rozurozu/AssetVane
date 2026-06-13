"""投信 NAV（基準価額）取得ジョブ — FundNavAdapter で NAV 時系列を取得し fund_navs に UPSERT する。

ADR-054: 投資信託の保有管理。登録済み funds の ISIN ごとに NAV を取得し fund_navs を前進させる。
ADR-010: 取得はアダプタ越し（FundNavAdapter）。ADR-002: 書き込みは UPSERT で冪等。
ADR-018: 個別 ISIN の失敗はジョブ境界で握り後続を継続。**全試行 ISIN が失敗したとき（総崩れ）
だけ ok=False**（1 本でも成功すれば ok=True・取得手段の無い投信 1 本で毎晩アラートを鳴らさない
割り切り）。fetch_index.py の構造をミラーする（差分は fetch_meta['fund_navs:<isin>'] で管理し
部分失敗から再開）。

snapshot_assets が当日の NAV から fund_value を焼くため、NIGHTLY_JOBS では snapshot_assets の
前に置き NAV を揃える。
"""

from __future__ import annotations

import logging
from datetime import date

from app.adapters.fund_nav import FundNavAdapter, FundNavFetchError
from app.batch.jobs._cursor import resolve_differential_start
from app.batch.runner import JobResult
from app.db import repo
from app.db.engine import get_engine

logger = logging.getLogger(__name__)

_SOURCE_PREFIX = "fund_navs"  # fetch_meta の source キー接頭辞


def _source_key(isin: str) -> str:
    """ISIN ごとの fetch_meta source キーを返す（例: 'fund_navs:JP90C000H1T1'）。"""
    return f"{_SOURCE_PREFIX}:{isin}"


def _start_date_for_isin(isin: str) -> str:
    """ISIN の取得開始日を fetch_meta から決める（差分取得・ADR-018 部分失敗からの再開）。

    fetch_meta 未存在 → 設定来の全履歴を取り込むため番兵 '1900-01-01' を返す（呼び出し側で
    from_=None＝全履歴に倒す。投信 CSV は常に全履歴を返すため十分過去の固定日で実質全履歴）。
    fetch_meta あり → last_fetched_date に重ねた地点（鮮度プローブ）。
    重ね・冪等の意図と重ね日数は resolve_differential_start（_cursor.py）に集約（ADR-018/002）。
    """
    with get_engine().connect() as conn:
        meta = repo.get_fetch_meta(conn, _source_key(isin))
        last_fetched = meta.get("last_fetched_date") if meta else None
    return resolve_differential_start(last_fetched, backfill_start="1900-01-01")


def run() -> JobResult:
    """登録済み投信の NAV を取得し fund_navs / fetch_meta を前進させる（ADR-054）。

    取得対象は repo.list_funds が返す登録済み funds の ISIN。各 ISIN を差分取得して UPSERT する。
    ISIN ごとに例外が発生しても後続 ISIN を継続する。**全試行 ISIN が失敗したとき（総崩れ）だけ
    ok=False** とし、1 本でも成功すれば ok=True（一部失敗は detail に内訳を残す）。協会コード未設定
    （CSV に必須）等で取得手段が無い投信 1 本のために毎晩バッチ失敗アラートを鳴らさない割り切り
    （fetch_index と同方針）。落ちた ISIN は fetch_meta の鮮度から digest が拾い可視化する。
    例外はジョブ境界で握り、総崩れ時のみ runner が Discord 通知する（ADR-018）。
    """
    today = date.today().isoformat()

    with get_engine().connect() as conn:
        funds = repo.list_funds(conn)

    if not funds:
        return JobResult(
            name="fetch_fund_navs",
            ok=True,
            rows=0,
            detail="登録済み投信なし（funds 0 件）。スキップ。",
        )

    total_rows = 0
    attempted = 0
    failed: list[str] = []

    adapter = FundNavAdapter()

    for fund in funds:
        isin = fund["isin"]
        assoc_code = fund.get("assoc_code")
        start = _start_date_for_isin(isin)
        # 初回番兵（1900-01-01）は from_=None（全履歴）として渡す。差分時は start を from_ に使う。
        from_ = None if start == "1900-01-01" else start

        attempted += 1
        try:
            rows = adapter.fetch_nav_history(isin, assoc_code=assoc_code, from_=from_, to=today)
            # isin/date が欠けた行は弾く（PK が NULL だと UPSERT が壊れる）。
            rows = [r for r in rows if r.get("isin") and r.get("date")]
            if rows:
                total_rows += repo.upsert_fund_navs(rows)
                # fetch_meta を最新取得日まで前進させる（ADR-018 部分失敗からの再開）。
                max_date = max(r["date"] for r in rows)
                repo.upsert_fetch_meta(_source_key(isin), max_date)
            else:
                # 空配列でも fetch_meta を today まで前進させる（再実行で空振りを繰り返さない）。
                repo.upsert_fetch_meta(_source_key(isin), today)
            logger.info(
                "fetch_fund_navs: %s %s〜%s・%d 行 UPSERT",
                isin,
                from_ or "(全履歴)",
                today,
                len(rows),
            )
        except (FundNavFetchError, Exception) as exc:  # noqa: BLE001 — ジョブ境界で握り後続継続
            logger.exception("fetch_fund_navs: %s が失敗（from_=%s）", isin, from_)
            # 直近試行の失敗を記録（last_fetched_date は据え置き）。digest が可視化する（ADR-018）。
            repo.mark_fetch_attempt_failed(_source_key(isin))
            failed.append(f"{isin}: {exc}")

    # 総崩れ（試行した全 ISIN が失敗）のときだけ失敗扱い＝runner が Discord 通知（ADR-018）。
    if attempted > 0 and len(failed) == attempted:
        return JobResult(
            name="fetch_fund_navs",
            ok=False,
            rows=total_rows,
            detail=f"全 {attempted} 投信取得失敗: {', '.join(failed)}",
        )

    detail = f"投信 {attempted}/{len(funds)} 件試行・{total_rows} 行 UPSERT（〜{today}）"
    if failed:
        detail += f"・取得不可 {len(failed)} 件: {', '.join(failed)}"
    return JobResult(name="fetch_fund_navs", ok=True, rows=total_rows, detail=detail)
