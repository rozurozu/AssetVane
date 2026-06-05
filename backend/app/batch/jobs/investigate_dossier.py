"""夜間バッチ: watchlist 巡回ドシエ調査ジョブ（phase4-spec.md §6・ADR-020/011/018）。

NIGHTLY_JOBS の末尾（事実が揃った後）で呼ばれる。同期ジョブとして JobResult を返す
（runner.py の規律）。内部は run_advisor.py の流儀に倣い、銘柄ごとに
`with get_engine().begin() as conn:` を開き `asyncio.run(investigate_stock(...))` で
非同期パイプライン（advisor/dossier.py）を駆動する（同期バッチから async を回す）。

巡回ロジック（phase4-spec.md §6・U-8/L-22 裁定）:
  - watchlist を `last_investigated_at` の**古い順**（未調査=None を最優先）に並べる。
  - **stale（21 日超 or 未調査）**の銘柄だけを対象にする（backend 算出・L-22）。
  - 先頭 **N=3 件**だけ `investigate_stock(conn, code, mode="nightly")` を回す。
    古い順 N 件/晩がコストを頭打ちにし、リストが大きくなってもコストは増えず古くなるだけ。

夜は **MCP 非依存で軽め**に回す（無人 cron でヘッドレスが使えないことがある＝ADR-020）。
取得手段の切替は dossier 側が `mode="nightly"` で吸収する。

部分失敗の握り（ADR-018・batch-pattern）: 1 銘柄が例外でも他の銘柄を止めない。各銘柄を
個別 try/except で握り detail に記録する。1 件でも失敗があれば JobResult.ok=False で返し、
runner が Discord に通知する（成功件数・失敗件数は detail に残す）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.advisor.dossier import investigate_stock
from app.batch.runner import JobResult
from app.db import repo
from app.db.engine import get_engine

logger = logging.getLogger(__name__)

# 毎晩巡回する件数 N（古い順・phase4-spec.md §6・U-8 既定値）。
# U-8 は「env 既定＋設定 UI ツマミ」を将来像とするが、config.py は本レーンの担当外のため
# 当面は定数で据える（settings へ昇格させるのは次イテレーション・報告参照）。
DOSSIER_NIGHTLY_COUNT = 3

# stale しきい値（日）。`last_investigated_at` がこれより古い（または未調査）銘柄を対象にする
# （backend 算出・phase4-spec.md §6・L-22）。
STALE_THRESHOLD_DAYS = 21


def _sort_key(item: dict[str, Any]) -> tuple[int, str]:
    """`last_investigated_at` の古い順ソートキー（未調査=None を最優先・spec §6）。

    None（未調査）は (0, "") で最優先、調査済みは (1, ISO文字列) で文字列昇順＝時刻昇順。
    ISO8601（UTC・Z/+00:00 固定の同形式）なので辞書順比較が時刻順と一致する。
    """
    last = item.get("last_investigated_at")
    if not last:
        return (0, "")
    return (1, last)


def _is_stale(last_investigated_at: str | None, now_iso: str) -> bool:
    """stale 判定（21 日超 or 未調査・spec §6・L-22）。

    未調査（None/空）は常に stale。調査済みは `now - last_investigated_at` が
    しきい値（21 日）を超えていれば stale。日付パース失敗時は安全側で stale 扱いにする。
    """
    if not last_investigated_at:
        return True
    from datetime import datetime

    try:
        last_dt = datetime.fromisoformat(last_investigated_at)
        now_dt = datetime.fromisoformat(now_iso)
    except ValueError:
        # パースできない値は安全側で再調査対象にする（古い/壊れた値を放置しない）。
        return True
    return (now_dt - last_dt).days > STALE_THRESHOLD_DAYS


def _select_targets(watchlist: list[dict[str, Any]], now_iso: str) -> list[dict[str, Any]]:
    """巡回対象を選ぶ: stale フィルタ → 古い順ソート → 先頭 N 件（spec §6）。"""
    stale = [w for w in watchlist if _is_stale(w.get("last_investigated_at"), now_iso)]
    stale.sort(key=_sort_key)
    return stale[:DOSSIER_NIGHTLY_COUNT]


def run() -> JobResult:
    """watchlist を古い順に巡回し stale な先頭 N 件のドシエを更新する（spec §6）。

    各銘柄を `with get_engine().begin() as conn:` で束ね（書き込みを atomic に）、
    `asyncio.run(investigate_stock(conn, code, mode="nightly"))` で非同期パイプラインを駆動する。
    1 銘柄が例外でも他を止めず detail に記録する（ADR-018）。失敗が 1 件でもあれば ok=False。
    """
    from datetime import UTC, datetime

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
                result = asyncio.run(investigate_stock(conn, code, mode="nightly"))
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
