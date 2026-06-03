"""プロンプト組み立てモジュール（軸1・軸2 共通）。

（advisor.md §6・spec §6.1・ADR-014/015/025）

CORE（不変・リポジトリ）＋ POLICY（可変・DB）＋ 手法カード ＋ 文脈 ＋ 画面コンテキスト ＋ 会話
の順序でメッセージ列を組み立て、LLM の `complete()` に渡せる形式を返す。

- 軸1（夜の分析AI）: screen_context=None で呼ぶ（ADR-025）。
- 軸2（相談チャット）: screen_context を渡す。数値は含めない（ADR-025）。
- 事実（facts）は messages に静的に積まない。
  Tool ループ（§4.2）で動的挿入（ADR-014 を構造的に担保）。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.advisor.policy_compiler import compile_policy

# ---------------------------------------------------------------------------
# データモデル（spec §6.2・_arbitration 決定3・B-4）
# ---------------------------------------------------------------------------


class Message(BaseModel):
    """チャット会話の 1 ターン。role は user/assistant のみ（system は build_messages が生成）。"""

    role: Literal["user", "assistant"]
    content: str


class FocusRef(BaseModel):
    """画面で主対象となっているエンティティへの参照（spec §6.2・ADR-025）。

    type で使い分け:
    - stock / signal → code（銘柄コード）を使う
    - portfolio / proposal → id（DB の PK）を使う
    """

    type: Literal["stock", "portfolio", "signal", "proposal"]
    code: str | None = None  # stock / signal
    id: int | None = None  # portfolio / proposal


class ScreenContext(BaseModel):
    """軸2 相談チャットが受け取る画面コンテキスト（ADR-025）。

    数値・画面データは含めない。「何の話をしているか」のヒントのみ。
    AI は数値が要れば該当 Tool を呼んで取り直す。
    """

    page: str  # "stock_detail" / "dashboard" / "signals" / "policy" / ...
    focus: FocusRef | None = None  # 対象が無いページは省略


# ---------------------------------------------------------------------------
# 画面コンテキストのコンパイル（spec §6.2・ADR-025）
# ---------------------------------------------------------------------------


def compile_screen_context(ctx: ScreenContext) -> str:
    """画面 context を 1 行の自然文へ変換する（数値は載せない・ADR-025）。

    例:
    - page="stock_detail", focus={type="stock", code="6920"}
      → 「ユーザーは銘柄 6920 の詳細ページを見ている」
    - page="dashboard", focus=None
      → 「ユーザーはダッシュボードを見ている」
    - page="proposals", focus={type="proposal", id=3}
      → 「ユーザーは提案 ID 3 のページを見ている」
    """
    focus = ctx.focus
    if focus is None:
        return f"ユーザーは {ctx.page} ページを見ている。"

    if focus.type in ("stock", "signal"):
        # code を使う
        if focus.code:
            return f"ユーザーは銘柄 {focus.code} の {ctx.page} ページを見ている。"
        return f"ユーザーは {ctx.page} ページを見ている。"

    if focus.type in ("portfolio", "proposal"):
        # id を使う
        if focus.id is not None:
            label = "ポートフォリオ" if focus.type == "portfolio" else "提案"
            return f"ユーザーは {label} ID {focus.id} の {ctx.page} ページを見ている。"
        return f"ユーザーは {ctx.page} ページを見ている。"

    # フォールバック
    return f"ユーザーは {ctx.page} ページを見ている。"


# ---------------------------------------------------------------------------
# メッセージ列の組み立て（spec §6.1）
# ---------------------------------------------------------------------------


def build_messages(
    *,
    core_prompt: str,
    policy: dict[str, object] | None,
    conversation: list[Message],
    screen_context: ScreenContext | None = None,
    method_cards: list[str] | None = None,
    recent_journal: str | None = None,
    facts: None = None,  # noqa: ARG001 — 事実は Tool ループで動的に入る（ADR-014）。静的には積まない
) -> list[dict[str, object]]:
    """advisor.md §6 / spec §6.1 の順序でメッセージ列を組む。

    組み立て順序（system → 会話）:
    1. system: [CORE]         ← core_prompt.md（不変・リポジトリ管理）
    2. system: [POLICY]       ← compile_policy(policy)（DB からコンパイル）
    3. system: [手法カード]    ← method_cards があれば（初期は None・ADR-016）
    4. system: [文脈]         ← recent_journal があれば「直近の投資日記: …」
    5. system: [画面コンテキスト] ← 軸2 のみ。compile_screen_context() の 1 行（ADR-025）
    6. conversation           ← user/assistant の列を {role, content} dict に変換

    facts 引数は受けるが使わない（将来用・None 固定）。事実は Tool ループで動的に挿入する。
    これにより「AI は計算しない（ADR-014）」を構造的に担保する。
    """
    messages: list[dict[str, object]] = []

    # 1. CORE（不変）
    messages.append({"role": "system", "content": core_prompt})

    # 2. POLICY（DB からコンパイル）
    policy_text = compile_policy(policy)
    messages.append({"role": "system", "content": policy_text})

    # 3. 手法カード（任意・初期は省略可・ADR-016）
    if method_cards:
        cards_text = "\n\n".join(method_cards)
        messages.append(
            {
                "role": "system",
                "content": f"## 手法カード（参照知識）\n\n{cards_text}",
            }
        )

    # 4. 文脈（直近の投資日記）
    if recent_journal:
        messages.append(
            {
                "role": "system",
                "content": f"## 直近の投資日記\n\n{recent_journal}",
            }
        )

    # 5. 画面コンテキスト（軸2 のみ。軸1 は screen_context=None で呼ぶ・ADR-025）
    if screen_context is not None:
        ctx_text = compile_screen_context(screen_context)
        messages.append({"role": "system", "content": ctx_text})

    # 6. 会話履歴（user / assistant の列）
    messages.extend({"role": m.role, "content": m.content} for m in conversation)

    return messages
