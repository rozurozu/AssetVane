"""EDINET DB（edinetdb.jp）接続設定の解決サービス（ADR-064・backend-service-quant-pattern）。

設計の真実: docs/decisions.md ADR-064（#2 売掛/在庫の質は edinetdb.jp の構造化財務・接続設定は
DB+WebUI）・ADR-061（J-Quants 設定の手本）・ADR-018（未設定時のフォールバック）。

第三者サービス edinetdb.jp の接続（api_key・契約プラン名）を DB から解決する単一点。repo（生クエリ）
と adapters/edinetdb（取得）の橋渡しで、未設定の意味づけ・プラン別のレート目安・例外を担う。
公式 EDINET（api.edinet-fsa.go.jp・DB の edinet_config・ADR-087）とは別系統（
命名 edinet/edinetdb で分離）。

レート制限の実 enforce は adapter がレスポンスの x-ratelimit-* ヘッダで行う（残予算を読む）。ここの
_PLAN_LIMITS はスロットル間隔と「1 晩あたりの取得上限（予備を残す目安）」を plan から引くだけ。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection

from app.db import repo
from app.db.engine import get_engine

_DEFAULT_PLAN = "free"


@dataclass(frozen=True)
class EdinetDbLimits:
    """plan 別のレート目安（ADR-064）。
    実 enforce はヘッダ＝ここは throttle と夜間ソフト上限の目安。"""

    min_interval_seconds: float  # スロットル間隔（取得を急ぎすぎない）
    nightly_soft_cap: int  # 1 晩あたりの取得上限（月予算に予備を残すための目安）
    daily_budget: int  # 参考: 日の上限（実 enforce はヘッダ x-ratelimit-remaining）
    monthly_budget: int  # 参考: 月の上限（実 enforce はヘッダ x-ratelimit-monthly-remaining）


# plan 名 → レート目安。free は実測値（日 100・月 600）。pro は契約時に実値へ更新する暫定値
# （実 enforce はヘッダ任せなので過大でも安全側＝ヘッダ残量で止まる）。
_PLAN_LIMITS: dict[str, EdinetDbLimits] = {
    "free": EdinetDbLimits(
        min_interval_seconds=1.0, nightly_soft_cap=30, daily_budget=100, monthly_budget=600
    ),
    "pro": EdinetDbLimits(
        min_interval_seconds=0.3, nightly_soft_cap=300, daily_budget=1000, monthly_budget=10000
    ),
}


def resolve_edinetdb_config(conn: Connection) -> dict[str, Any] | None:
    """edinetdb.jp 接続を DB から解決する（ADR-064）。

    api_key が空（未登録）なら None（＝未設定。呼び出し側が疎通 configured=False や #2 取得の静かな
    skip に倒す）。戻り値は {api_key, plan}。plan は空なら "free" に正規化（
    未知 plan は plan_limits が
    free に倒すため、ここでは中継するだけ）。
    """
    row = repo.get_edinetdb_config(conn)
    if row is None:
        return None
    api_key = (row.get("api_key") or "").strip()
    if not api_key:
        return None
    plan = (row.get("plan") or "").strip().lower() or _DEFAULT_PLAN
    return {"api_key": api_key, "plan": plan}


def current_plan(conn: Connection) -> str:
    """設定中の契約プラン名を返す（未登録は "free"・ADR-064）。meta 表示・
    plan_limits 解決に使う。"""
    row = repo.get_edinetdb_config(conn)
    if row is None:
        return _DEFAULT_PLAN
    return (row.get("plan") or "").strip().lower() or _DEFAULT_PLAN


def plan_limits(plan: str | None) -> EdinetDbLimits:
    """plan 名からレート目安を引く（未知 plan は free に倒す＝最安全・ADR-064）。"""
    key = (plan or _DEFAULT_PLAN).strip().lower()
    return _PLAN_LIMITS.get(key, _PLAN_LIMITS[_DEFAULT_PLAN])


def build_edinetdb_adapter(conn: Connection | None = None):  # noqa: ANN201 — 戻り値は EdinetDbAdapter
    """DB の接続設定から EdinetDbAdapter を生成するファクトリ（ADR-064・ADR-010）。

    全構成点（#2 夜間ジョブ・診断）はここを通す。conn 未指定なら短命 read 接続を開いて解決する
    （ジョブは conn を持たないため）。未設定（api_key 空）は EdinetDbAdapterError を投げる（
    #2 ジョブは
    握って静かに skip＝ADR-064。
    診断は resolve_edinetdb_config の None で configured=False 判定する）。
    スロットル間隔は plan から決める（_PLAN_LIMITS）。
    """
    from app.adapters.edinetdb import EdinetDbAdapter, EdinetDbAdapterError

    if conn is None:
        with get_engine().connect() as c:
            cfg = resolve_edinetdb_config(c)
    else:
        cfg = resolve_edinetdb_config(conn)

    if cfg is None:
        raise EdinetDbAdapterError(
            "EDINET DB（edinetdb.jp）API キーが未設定です。"
            "/settings の「EDINET DB 設定」から登録してください（ADR-064）。"
        )
    limits = plan_limits(cfg["plan"])
    return EdinetDbAdapter(api_key=cfg["api_key"], min_interval_seconds=limits.min_interval_seconds)
