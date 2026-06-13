"""夜間バッチ: watchlist 巡回ドシエ調査ジョブ（phase4-spec.md §6・ADR-020/033/011/018）。

NIGHTLY_JOBS の末尾（事実が揃った後）で呼ばれる。同期ジョブとして JobResult を返す
（runner.py の規律）。内部は run_advisor.py の流儀に倣い、銘柄ごとに
`with get_engine().begin() as conn:` を開き `asyncio.run(investigate_stock(...))` で
非同期パイプライン（advisor/dossier.py）を駆動する（同期バッチから async を回す）。

巡回ロジック（phase4-spec.md §6・ADR-033）:
  - 全銘柄 stale=21 日固定・夜あたり N=3 固定はやめ、**watchlist 銘柄ごとの調査間隔
    （`interval_days`・既定 21）** で stale を判定する（ADR-033）。各 watchlist 行は
    「未調査(None) もしくは `now - last_investigated_at` がその銘柄の `interval_days` 日を
    超えた」なら対象。`interval_days` は list_watchlist が常に非 NULL（既定 21）で返す。
  - 対象を `last_investigated_at` の**古い順**（未調査=None を最優先）に並べる。
  - 夜あたりの処理本数は `settings.dossier_nightly_max`（config・暴走防止の天井）で `[:cap]`。
    間隔を短く設定した銘柄が多くても 1 晩のコストは天井で頭打ちになる（古くなるだけ）。

夜は **MCP 非依存で軽め**に回す（無人 cron でヘッドレスが使えないことがある＝ADR-020）。
取得手段は httpx 一本に統一したため `mode` は廃止した（ADR-020 改訂）。

部分失敗の握り（ADR-018・batch-pattern）: 1 銘柄が例外でも他の銘柄を止めない。各銘柄を
個別 try/except で握り detail に記録する。1 件でも失敗があれば JobResult.ok=False で返し、
runner が Discord に通知する（成功件数・失敗件数は detail に残す）。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from app.advisor.dossier import investigate_stock
from app.batch.runner import JobResult
from app.config import settings
from app.db import repo
from app.db.engine import get_engine

logger = logging.getLogger(__name__)

# interval_days が欠損（万一 NULL）だった場合に使う既定間隔（日）。list_watchlist は常に
# 非 NULL（既定 21）で返す契約だが、防御的に同値を据える（ADR-033）。
_DEFAULT_INTERVAL_DAYS = 21


def _sort_key(item: dict[str, Any]) -> tuple[int, str]:
    """`last_investigated_at` の古い順ソートキー（未調査=None を最優先・spec §6）。

    None（未調査）は (0, "") で最優先、調査済みは (1, ISO文字列) で文字列昇順＝時刻昇順。
    ISO8601（UTC・Z/+00:00 固定の同形式）なので辞書順比較が時刻順と一致する。
    """
    last = item.get("last_investigated_at")
    if not last:
        return (0, "")
    return (1, last)


def _is_stale(last_investigated_at: str | None, interval_days: int, now_iso: str) -> bool:
    """stale 判定（per-stock `interval_days` 超 or 未調査・spec §6・ADR-033）。

    未調査（None/空）は常に stale。調査済みは `now - last_investigated_at` がその銘柄の
    `interval_days` 日を超えていれば stale。日付パース失敗時は安全側で stale 扱いにする。
    """
    if not last_investigated_at:
        return True
    try:
        last_dt = datetime.fromisoformat(last_investigated_at)
        now_dt = datetime.fromisoformat(now_iso)
    except ValueError:
        # パースできない値は安全側で再調査対象にする（古い/壊れた値を放置しない）。
        return True
    return (now_dt - last_dt).days > interval_days


def _select_targets(watchlist: list[dict[str, Any]], now_iso: str) -> list[dict[str, Any]]:
    """巡回対象を選ぶ: per-stock interval_days で stale フィルタ → 古い順 → 天井（ADR-033）。

    stale しきい値は固定 21 ではなく各 watchlist 行の `interval_days`（既定 21・list_watchlist が
    常に非 NULL で返す）。古い順に並べ、夜あたり上限 `settings.dossier_nightly_max` で打ち切る。
    """
    stale = [
        w
        for w in watchlist
        if _is_stale(
            w.get("last_investigated_at"),
            int(w.get("interval_days") or _DEFAULT_INTERVAL_DAYS),
            now_iso,
        )
    ]
    stale.sort(key=_sort_key)
    return stale[: settings.dossier_nightly_max]


def run() -> JobResult:
    """watchlist を per-stock interval_days で stale 判定し古い順に天井まで巡回（ADR-033）。

    各銘柄を `with get_engine().begin() as conn:` で束ね（書き込みを atomic に）、
    `asyncio.run(investigate_stock(conn, code))` で非同期パイプラインを駆動する（mode は廃止）。
    1 銘柄が例外でも他を止めず detail に記録する（ADR-018）。失敗が 1 件でもあれば ok=False。
    """
    now_iso = datetime.now(UTC).isoformat()

    try:
        with get_engine().connect() as conn:
            watchlist = repo.list_watchlist(conn)
    except Exception as exc:  # noqa: BLE001 — ジョブ境界で握り runner に返す
        logger.exception("investigate_dossier: watchlist 取得に失敗")
        return JobResult(
            name="investigate_dossier", ok=False, rows=0, detail=f"watchlist 取得失敗: {exc}"
        )

    targets = _select_targets(watchlist, now_iso)
    if not targets:
        return JobResult(
            name="investigate_dossier",
            ok=True,
            rows=0,
            detail=f"巡回対象なし（watchlist {len(watchlist)} 件・stale 無し）",
        )

    n_ok = 0
    n_sources = 0
    failures: list[str] = []
    for item in targets:
        code = item["code"]
        try:
            # 銘柄ごとに begin() で束ねる（複数ソース＋ドシエ本体を atomic に・dossier W2 規約）。
            with get_engine().begin() as conn:
                result = asyncio.run(investigate_stock(conn, code))
            n_ok += 1
            n_sources += int(result.get("n_sources_added", 0))
        except Exception as exc:  # noqa: BLE001 — 銘柄境界で握り後続銘柄を止めない（ADR-018）
            logger.exception("investigate_dossier: 銘柄 %s の調査に失敗", code)
            failures.append(f"{code}: {exc}")

    detail = (
        f"巡回 {len(targets)} 件中 成功 {n_ok}・失敗 {len(failures)}（新ソース {n_sources} 件）"
    )
    if failures:
        detail += " / 失敗詳細: " + "; ".join(failures)

    return JobResult(
        name="investigate_dossier",
        ok=not failures,
        rows=n_ok,
        detail=detail,
    )
