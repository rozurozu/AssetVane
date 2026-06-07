"""手法カード（参照知識③）のロード（ADR-016/048）。

リポジトリ管理の markdown カード（`backend/app/advisor/cards/*.md`）を起動時に 1 度だけ読み、
`build_messages` の `method_cards` に**常時注入**する（ADR-016 の「全列挙」段階）。CORE
（core_prompt.md）と同じく、チャットでは書き換えない不変資産（jj 版管理）。カードはファイル名
昇順で並べて連結する。

設計の位置づけ:
- これは ADR-016③（参照知識・計算なし）の runtime 注入版。計算は必ずコード（Tool / quant）側に
  あり、カードは「いつ・どう解釈するか」の作法だけを持つ（ADR-014）。
- 置き場所が `docs/methods/`（lead-lag.md 等の設計参照カード）ではなく backend 配下なのは、
  本番イメージで backend だけが配布される（docs/ は同梱されない）ため。runtime に注入するカードは
  core_prompt.md と同じく backend のプロンプト資産として持つ（モジュール名は cards/ ディレクトリ
  との import 衝突を避けて method_cards.py）。
- **近接の planned 項目（ADR-048）**: カードが増えたら、メタデータだけ常時露出・本文は選ばれた時に
  ロードする on-demand 機構（progressive disclosure）へ移す。今は全カードを常時注入する。
"""

from __future__ import annotations

from pathlib import Path

_CARDS_DIR = Path(__file__).parent / "cards"


def load_method_cards() -> list[str]:
    """`cards/*.md` を名前順に読んで本文リストで返す（無ければ空＝注入なし）。"""
    if not _CARDS_DIR.exists():
        return []
    return [p.read_text(encoding="utf-8") for p in sorted(_CARDS_DIR.glob("*.md"))]


# 起動時に 1 度だけ読む（不変資産・ADR-015/016）。build_messages へ常時渡す。
METHOD_CARDS: list[str] = load_method_cards()
