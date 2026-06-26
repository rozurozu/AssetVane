"""知識カードのプロンプト注入向けロード（ADR-062）。

設計の真実: docs/decisions.md ADR-062（知識カード基盤）。

旧・手法カード（method_cards.py の起動時 1 度ロード・全カード常時注入）を置き換える。カードは UI で
随時増減するため、起動時固定ではなく呼び出しのたびに DB から active 行を読む（軸1/軸2 の各ターン）。

フェーズ1（足場）は active 行を全注入する（always_inject 含む）。フェーズ2 で when_to_apply の意味
検索（retrieval）に置き換える（ADR-045 同型・このモジュールの差し替えで両軸に波及）。
"""

from __future__ import annotations

from typing import Any

from app.db import repo
from app.db.engine import get_engine


def _format_card(row: dict[str, Any]) -> str:
    """1 カードを注入用テキストへ（タイトル見出し＋本文）。"""
    title = str(row.get("title") or "").strip()
    body = str(row.get("body") or "").strip()
    return f"### {title}\n{body}" if title else body


def load_active_card_texts() -> list[str]:
    """注入対象（status='active'）の知識カードを整形済みテキストの list で返す（ADR-062）。

    build_messages の knowledge_cards 引数に渡す。カードが 0 件なら空 list（注入なし）。読み取り専用
    なので自前で短く connect を開閉する。
    """
    with get_engine().connect() as conn:
        rows = repo.list_active_knowledge_cards(conn)
    return [_format_card(r) for r in rows]
