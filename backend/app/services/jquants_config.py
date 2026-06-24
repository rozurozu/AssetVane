"""J-Quants 接続設定の解決サービス（ADR-061・backend-service-quant-pattern）。

設計の真実: docs/decisions.md ADR-061（J-Quants の api_key/plan を env→DB+WebUI に移管）・
ADR-008（V2・プラン）・ADR-018（未設定時のフォールバック）。

J-Quants V2 の接続（api_key・契約プラン名）を DB から解決する単一点。repo（生クエリ）と
adapters/jquants（取得）の橋渡しで、未設定の意味づけと例外を担う。env は撤去したため
（ADR-061）、アダプタは settings を読まず、ここで解決した値を渡される（DB に触れるのは FastAPI＝
ADR-005。バッチ/スクリプトも同プロセスで engine 越しに読む）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Connection

from app.db import repo
from app.db.engine import get_engine

_DEFAULT_PLAN = "free"


def resolve_jquants_config(conn: Connection) -> dict[str, Any] | None:
    """J-Quants 接続を DB から解決する（ADR-061）。

    api_key が空（未登録）なら None（＝未設定。呼び出し側が JQuantsError や疎通 configured=False
    に倒す）。戻り値は {api_key, plan}。plan は空なら "free" に正規化（未知プラン名はアダプタが
    free に倒すため、ここでは中継するだけ）。
    """
    row = repo.get_jquants_config(conn)
    if row is None:
        return None
    api_key = (row.get("api_key") or "").strip()
    if not api_key:
        return None
    plan = (row.get("plan") or "").strip().lower() or _DEFAULT_PLAN
    return {"api_key": api_key, "plan": plan}


def current_plan(conn: Connection) -> str:
    """設定中の契約プラン名を返す（未登録は "free"・ADR-061）。

    lead_lag の遅延判定（plan=='free'）と meta.plan 表示に使う。api_key 未登録でも plan 行があれば
    その値を返し、行が無ければ最も安全な "free"（遅延扱い）に倒す。
    """
    row = repo.get_jquants_config(conn)
    if row is None:
        return _DEFAULT_PLAN
    return (row.get("plan") or "").strip().lower() or _DEFAULT_PLAN


def build_jquants_adapter(conn: Connection | None = None):  # noqa: ANN201 — 戻り値は JQuantsAdapter
    """DB の接続設定から JQuantsAdapter を生成するファクトリ（ADR-061・ADR-010）。

    全構成点（夜間バッチ・スクリプト・診断・index の TOPIX フォールバック）はここを通す。conn 未指定
    なら短命 read 接続を開いて解決する（バッチ等は conn を持たないため）。未設定（api_key 空）は
    JQuantsError を投げる（バッチが握って Discord に畳む＝従来の「キー未設定で落ちる」挙動と同じ・
    ADR-018）。スロットル間隔はアダプタが plan から決める（_PLAN_INTERVALS）。
    """
    from app.adapters.jquants import JQuantsAdapter, JQuantsError

    if conn is None:
        with get_engine().connect() as c:
            cfg = resolve_jquants_config(c)
    else:
        cfg = resolve_jquants_config(conn)

    if cfg is None:
        raise JQuantsError(
            "J-Quants API キーが未設定です。"
            "/settings の「J-Quants 設定」から登録してください（ADR-061）。"
        )
    return JQuantsAdapter(api_key=cfg["api_key"], plan=cfg["plan"])
