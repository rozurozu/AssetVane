"""公式 EDINET（api.edinet-fsa.go.jp）接続設定の解決サービス（ADR-087・backend-service-quant）。

設計の真実: docs/decisions.md ADR-087（公式 EDINET の Subscription-Key を env→DB+WebUI へ移す）・
ADR-061/064（J-Quants/edinetdb 設定の手本）・ADR-018（未設定時のフォールバック）。

公式 EDINET の接続（api_key＝Subscription-Key）を DB から解決する単一点。repo（生クエリ）と
adapters/edinet（取得）の橋渡しで、未設定の意味づけ・例外を担う。plan 概念は無い（公式 EDINET は
回数クォータ無し・レート制限のみ＝スロットル間隔は非秘密つまみで config に残す）。第三者
edinetdb.jp（services/edinetdb_config）とは別系統（命名 edinet/edinetdb で分離）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Connection

from app.db import repo
from app.db.engine import get_engine


def resolve_edinet_config(conn: Connection) -> dict[str, Any] | None:
    """公式 EDINET 接続を DB から解決する（ADR-087）。

    api_key が空（未登録）なら None（＝未設定。呼び出し側が疎通 configured=False や段階C 取得の
    静かな skip に倒す）。戻り値は {api_key}。plan は持たない（公式 EDINET はプラン階層が無い）。
    """
    row = repo.get_edinet_config(conn)
    if row is None:
        return None
    api_key = (row.get("api_key") or "").strip()
    if not api_key:
        return None
    return {"api_key": api_key}


def build_edinet_adapter(conn: Connection | None = None):  # noqa: ANN201 — 戻り値は EdinetAdapter
    """DB の接続設定から EdinetAdapter を生成するファクトリ（ADR-087・ADR-010）。

    全構成点（段階C 夜間ジョブ・バックフィル script・診断）はここを通す。conn 未指定なら短命 read
    接続を開いて解決する（ジョブ/script は conn 無し）。未設定（api_key 空）は EdinetAdapterError
    を投げる（夜間ジョブは resolve の None で静かに skip・診断は resolve の None で configured=False
    判定するため、実取得系だけがこの例外に当たる）。base_url/timeout/スロットル間隔は非秘密つまみとして
    引き続き settings（env）から解決する（ADR-087・adapters/edinet.py の __init__）。
    """
    from app.adapters.edinet import EdinetAdapter, EdinetAdapterError

    if conn is None:
        with get_engine().connect() as c:
            cfg = resolve_edinet_config(c)
    else:
        cfg = resolve_edinet_config(conn)

    if cfg is None:
        raise EdinetAdapterError(
            "公式 EDINET（api.edinet-fsa.go.jp）API キーが未設定です。"
            "/settings の「EDINET 設定」から登録してください（ADR-087）。"
        )
    return EdinetAdapter(api_key=cfg["api_key"])
