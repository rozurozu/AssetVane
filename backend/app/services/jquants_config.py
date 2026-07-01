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

# プラン別の株価遅延日数（docs/jquants.md・ADR-008）。Free は 12 週遅延＝84 日、
# 有料プラン（light/standard/premium）は遅延なし。右上バッジの表示に使う事実値で、
# フロントにハードコードせず backend の定数から /health 経由で配る（ADR-014・ADR-061）。
# レートの _PLAN_INTERVALS（adapters/jquants.py）と対をなす「プラン → 数値」の単一定義点。
_PLAN_DELAY_DAYS: dict[str, int] = {
    "free": 84,  # 12 週 = 84 日
    "light": 0,
    "standard": 0,
    "premium": 0,
}


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


def plan_status(conn: Connection) -> dict[str, Any]:
    """右上バッジ用のプラン状態 {plan, delay_days, configured}（ADR-061・ADR-014）。

    plan は current_plan と同じ正規化（未登録/空は "free"）。delay_days は _PLAN_DELAY_DAYS を
    引き、未知プラン名は最安全（遅延あり=free 相当の 84 日）に倒す。configured は api_key の有無
    （resolve_jquants_config と同じ判定）で、未設定なら /settings 誘導表示に使う。行は 1 回読む。
    """
    row = repo.get_jquants_config(conn)
    api_key = (row.get("api_key") or "").strip() if row else ""
    plan = (row.get("plan") or "").strip().lower() if row else ""
    plan = plan or _DEFAULT_PLAN
    return {
        "plan": plan,
        "delay_days": _PLAN_DELAY_DAYS.get(plan, _PLAN_DELAY_DAYS[_DEFAULT_PLAN]),
        "configured": bool(api_key),
    }


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
