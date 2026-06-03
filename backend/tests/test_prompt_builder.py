"""prompt_builder のユニットテスト。

DB 非依存・LLM 非依存の純粋関数テスト。
（spec §10・ADR-014/015/025）
"""

from __future__ import annotations

import pytest

from app.advisor.prompt_builder import (
    FocusRef,
    Message,
    ScreenContext,
    build_messages,
    compile_screen_context,
)

# ---------------------------------------------------------------------------
# テスト用フィクスチャ
# ---------------------------------------------------------------------------

CORE = "## テスト CORE プロンプト"

POLICY_DICT: dict[str, object] = {
    "risk_tolerance": "高",
    "time_horizon": "短",
    "no_leverage": 1,
}

CONVERSATION: list[Message] = [
    Message(role="user", content="銘柄 6920 を教えて"),
    Message(role="assistant", content="はい、確認します"),
]


# ---------------------------------------------------------------------------
# compile_screen_context
# ---------------------------------------------------------------------------


class TestCompileScreenContext:
    """compile_screen_context の単体テスト。"""

    def test_stock_uses_code(self) -> None:
        """focus.type=stock のとき code を使う。"""
        ctx = ScreenContext(page="stock_detail", focus=FocusRef(type="stock", code="6920"))
        result = compile_screen_context(ctx)
        assert "6920" in result

    def test_signal_uses_code(self) -> None:
        """focus.type=signal のとき code を使う。"""
        ctx = ScreenContext(page="signals", focus=FocusRef(type="signal", code="7203"))
        result = compile_screen_context(ctx)
        assert "7203" in result

    def test_portfolio_uses_id(self) -> None:
        """focus.type=portfolio のとき id を使う。"""
        ctx = ScreenContext(page="portfolio", focus=FocusRef(type="portfolio", id=1))
        result = compile_screen_context(ctx)
        assert "1" in result
        assert "ポートフォリオ" in result

    def test_proposal_uses_id(self) -> None:
        """focus.type=proposal のとき id を使う。"""
        ctx = ScreenContext(page="proposals", focus=FocusRef(type="proposal", id=3))
        result = compile_screen_context(ctx)
        assert "3" in result
        assert "提案" in result

    def test_no_focus_shows_page(self) -> None:
        """focus=None のときページ名を含む文を返す。"""
        ctx = ScreenContext(page="dashboard")
        result = compile_screen_context(ctx)
        assert "dashboard" in result

    def test_returns_single_line(self) -> None:
        """結果は改行を含まない 1 行文字列。"""
        ctx = ScreenContext(page="stock_detail", focus=FocusRef(type="stock", code="6758"))
        result = compile_screen_context(ctx)
        assert "\n" not in result


# ---------------------------------------------------------------------------
# build_messages — 層の順序
# ---------------------------------------------------------------------------


class TestBuildMessagesOrder:
    """build_messages が正しい順序でメッセージ列を組む。"""

    def test_first_message_is_core(self) -> None:
        """先頭は CORE の system メッセージ。"""
        msgs = build_messages(
            core_prompt=CORE,
            policy=POLICY_DICT,
            conversation=CONVERSATION,
        )
        assert msgs[0]["role"] == "system"
        assert CORE in str(msgs[0]["content"])

    def test_second_message_is_policy(self) -> None:
        """2 番目は POLICY の system メッセージ（compile_policy の出力）。"""
        msgs = build_messages(
            core_prompt=CORE,
            policy=POLICY_DICT,
            conversation=CONVERSATION,
        )
        assert msgs[1]["role"] == "system"
        # compile_policy が生成する文を一部含む
        assert "リスク許容度" in str(msgs[1]["content"])

    def test_order_core_policy_method_journal_context_conversation(self) -> None:
        """全レイヤを渡したとき CORE→POLICY→手法→文脈→context→会話 の順になる。"""
        ctx = ScreenContext(page="stock_detail", focus=FocusRef(type="stock", code="9984"))
        msgs = build_messages(
            core_prompt=CORE,
            policy=POLICY_DICT,
            conversation=CONVERSATION,
            screen_context=ctx,
            method_cards=["手法カード: モメンタム"],
            recent_journal="昨日の日記テキスト",
        )
        roles = [m["role"] for m in msgs]
        contents = [str(m["content"]) for m in msgs]

        # すべて system が先、会話が後
        system_indices = [i for i, r in enumerate(roles) if r == "system"]
        conv_indices = [i for i, r in enumerate(roles) if r in ("user", "assistant")]
        assert max(system_indices) < min(conv_indices)

        # CORE が最初の system
        assert CORE in contents[system_indices[0]]

        # POLICY が 2 番目の system
        assert "リスク許容度" in contents[system_indices[1]]

        # 手法カードが含まれる
        method_idx = next(i for i, c in enumerate(contents) if "手法カード" in c)
        assert roles[method_idx] == "system"

        # 文脈が含まれる
        journal_idx = next(i for i, c in enumerate(contents) if "昨日の日記テキスト" in c)
        assert roles[journal_idx] == "system"

        # 画面コンテキストが含まれる
        ctx_idx = next(i for i, c in enumerate(contents) if "9984" in c)
        assert roles[ctx_idx] == "system"

        # 手法→文脈→context の順
        assert method_idx < journal_idx < ctx_idx

        # 会話が context の後
        assert ctx_idx < conv_indices[0]

    def test_conversation_messages_are_preserved(self) -> None:
        """会話の role と content が正しく保持される。"""
        msgs = build_messages(
            core_prompt=CORE,
            policy=POLICY_DICT,
            conversation=CONVERSATION,
        )
        user_msgs = [m for m in msgs if m["role"] == "user"]
        asst_msgs = [m for m in msgs if m["role"] == "assistant"]
        assert len(user_msgs) == 1
        assert "6920" in str(user_msgs[0]["content"])
        assert len(asst_msgs) == 1


# ---------------------------------------------------------------------------
# build_messages — 軸1（screen_context=None）
# ---------------------------------------------------------------------------


class TestAxis1NoScreenContext:
    """軸1（夜の分析AI）は screen_context=None で呼ぶ（ADR-025）。"""

    def test_no_screen_context_system_when_none(self) -> None:
        """screen_context=None のとき画面コンテキスト system メッセージが出ない。"""
        msgs = build_messages(
            core_prompt=CORE,
            policy=POLICY_DICT,
            conversation=[Message(role="user", content="夜の分析を実行")],
            screen_context=None,
        )
        # system メッセージの中に「ページを見ている」を含むものが無いことを確認
        screen_msgs = [
            m for m in msgs if m["role"] == "system" and "ページを見ている" in str(m["content"])
        ]
        assert len(screen_msgs) == 0

    def test_with_screen_context_adds_system_message(self) -> None:
        """screen_context があれば画面コンテキスト system メッセージが追加される。"""
        ctx = ScreenContext(page="dashboard")
        msgs_with = build_messages(
            core_prompt=CORE,
            policy=POLICY_DICT,
            conversation=CONVERSATION,
            screen_context=ctx,
        )
        msgs_without = build_messages(
            core_prompt=CORE,
            policy=POLICY_DICT,
            conversation=CONVERSATION,
            screen_context=None,
        )
        assert len(msgs_with) == len(msgs_without) + 1


# ---------------------------------------------------------------------------
# build_messages — facts は静的に積まれない（ADR-014 の構造的担保）
# ---------------------------------------------------------------------------


class TestFactsNotStatic:
    """facts 引数は受けるが messages に静的に積まない（ADR-014）。"""

    def test_facts_none_does_not_add_message(self) -> None:
        """facts=None（デフォルト）のとき余分な system メッセージが増えない。"""
        msgs_default = build_messages(
            core_prompt=CORE,
            policy=POLICY_DICT,
            conversation=CONVERSATION,
        )
        msgs_with_facts = build_messages(
            core_prompt=CORE,
            policy=POLICY_DICT,
            conversation=CONVERSATION,
            facts=None,
        )
        assert len(msgs_default) == len(msgs_with_facts)

    def test_no_facts_role_in_messages(self) -> None:
        """messages に role='tool' や facts を示す system が含まれない。"""
        msgs = build_messages(
            core_prompt=CORE,
            policy=POLICY_DICT,
            conversation=CONVERSATION,
        )
        for m in msgs:
            assert m["role"] in ("system", "user", "assistant")


# ---------------------------------------------------------------------------
# build_messages — policy=None でも壊れない
# ---------------------------------------------------------------------------


def test_build_messages_policy_none_does_not_raise() -> None:
    """policy=None のときも例外を投げず、POLICY system メッセージが生成される。"""
    msgs = build_messages(
        core_prompt=CORE,
        policy=None,
        conversation=[Message(role="user", content="こんにちは")],
    )
    policy_msgs = [
        m for m in msgs if m["role"] == "system" and "まだ設定されていない" in str(m["content"])
    ]
    assert len(policy_msgs) == 1


# ---------------------------------------------------------------------------
# build_messages — method_cards / recent_journal が省略可能
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"method_cards": None},
        {"recent_journal": None},
        {"method_cards": None, "recent_journal": None},
    ],
)
def test_build_messages_optional_fields_do_not_raise(kwargs: dict) -> None:  # type: ignore[type-arg]
    """省略可能フィールドが None/未指定でも例外を投げない。"""
    msgs = build_messages(
        core_prompt=CORE,
        policy=POLICY_DICT,
        conversation=CONVERSATION,
        **kwargs,
    )
    assert len(msgs) >= 2  # CORE + POLICY は必ず入る


def test_build_messages_method_cards_adds_system_message() -> None:
    """method_cards を渡すと手法カード system メッセージが追加される。"""
    msgs = build_messages(
        core_prompt=CORE,
        policy=POLICY_DICT,
        conversation=CONVERSATION,
        method_cards=["モメンタム手法", "RSI 手法"],
    )
    card_msgs = [m for m in msgs if m["role"] == "system" and "モメンタム手法" in str(m["content"])]
    assert len(card_msgs) == 1


def test_build_messages_recent_journal_adds_system_message() -> None:
    """recent_journal を渡すと文脈 system メッセージが追加される。"""
    msgs = build_messages(
        core_prompt=CORE,
        policy=POLICY_DICT,
        conversation=CONVERSATION,
        recent_journal="先週の投資日記サマリ",
    )
    journal_msgs = [
        m for m in msgs if m["role"] == "system" and "先週の投資日記サマリ" in str(m["content"])
    ]
    assert len(journal_msgs) == 1
