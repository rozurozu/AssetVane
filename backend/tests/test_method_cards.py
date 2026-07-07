"""手法カード（ローダ＋get_method_card Tool）を検証する（ADR-075）。

担保: frontmatter パース・未登録は None・index/カタログ・ドリフト検査・Tool handler の
（本文/一覧/未登録）3 分岐。リポジトリの advisor/method_cards/*.md を実際に読む（DB/ネット非依存）。
"""

from __future__ import annotations

import asyncio

from app.advisor import method_cards
from app.advisor.tools import handlers

# 現時点で method_cards を持つべき signal_type（ドリフト検査の基準・手法追加時はここも更新）。
_KNOWN = {"momentum", "volume_spike", "stealth_accum", "lead_lag", "ai_alpha"}


def test_loader_parses_frontmatter() -> None:
    """<signal_type>.md の frontmatter（signal_type/summary）と本文を読める。"""
    card = method_cards.get_method_card("lead_lag")
    assert card is not None
    assert card["signal_type"] == "lead_lag"
    assert card["summary"]  # 空でない
    assert "リードラグ" in card["body"]


def test_unknown_returns_none() -> None:
    """未登録の signal_type は None。"""
    assert method_cards.get_method_card("nope") is None


def test_index_covers_known() -> None:
    """index が既知の全 signal_type を summary 付きで返す。"""
    index = method_cards.method_card_index()
    got = {c["signal_type"] for c in index}
    assert _KNOWN <= got
    assert all(c["summary"] for c in index)


def test_no_drift() -> None:
    """既知 signal_type に対しカードの書き忘れ（missing）も孤児（orphan）も無い。"""
    drift = method_cards.validate_method_cards(_KNOWN)
    assert drift["missing"] == []
    assert drift["orphan"] == []


def test_strategy_card_loads_with_kind() -> None:
    """kind=strategy の手法カード（清原式）が読め、kind を持つ（ADR-079）。"""
    card = method_cards.get_method_card("net_cash_value")
    assert card is not None
    assert card["kind"] == "strategy"
    assert "ネットキャッシュ" in card["body"]


def test_signal_cards_default_kind_signal() -> None:
    """kind 未記載の既存カードは kind=signal 既定になる（後方互換・ADR-079）。"""
    card = method_cards.get_method_card("momentum")
    assert card is not None
    assert card["kind"] == "signal"


def test_strategy_card_not_flagged_as_orphan() -> None:
    """strategy 種は signal に紐づかないのでドリフト検査の orphan にならない（ADR-079）。"""
    drift = method_cards.validate_method_cards(_KNOWN)
    assert "net_cash_value" not in drift["orphan"]
    assert drift["orphan"] == []


def test_catalog_tags_strategy_cards() -> None:
    """カタログは strategy 種に [strategy] タグを付けて能動 screen 手法だと示す（ADR-079）。"""
    catalog = method_cards.catalog_text()
    assert "net_cash_value [strategy]:" in catalog
    # signal 種はタグ無しのまま（後方互換）
    assert "- momentum:" in catalog


def test_catalog_text_lists_cards() -> None:
    """Tool description 用カタログに独自手法が並ぶ（常時露出のメタ）。"""
    catalog = method_cards.catalog_text()
    assert "lead_lag" in catalog
    assert "stealth_accum" in catalog


# --- native_horizon（ADR-091・手法ごとの想定時間軸）---------------------------


def test_all_cards_declare_native_horizon() -> None:
    """全カード（signal＋strategy）が native_horizon を宣言する（相談の時間軸に手法を合わせる）。"""
    index = method_cards.method_card_index()
    for c in index:
        assert c["native_horizon"], f"{c['signal_type']} に native_horizon が無い"
    # get_method_card 経由でも取れる（未指定既定の空文字ではない）。
    assert method_cards.get_method_card("momentum")["native_horizon"]
    assert method_cards.get_method_card("net_cash_value")["native_horizon"]


def test_catalog_shows_native_horizon() -> None:
    """カタログ（Tool description 常時露出）に時間軸が出て advisor が手法を選別できる。"""
    catalog = method_cards.catalog_text()
    assert "時間軸:" in catalog
    # lead_lag は day（翌営業日）と明示され保有ホライズンでないと分かる。
    assert "day" in catalog


def test_handler_returns_card() -> None:
    """get_method_card(signal_type) は found=True＋本文を返す。"""
    out = asyncio.run(handlers.handle_get_method_card({"signal_type": "stealth_accum"}))
    assert out["found"] is True
    assert out["signal_type"] == "stealth_accum"
    assert "仕込み" in out["body"]


def test_handler_no_arg_returns_index() -> None:
    """引数なしは登録カード一覧（available）を返す。"""
    out = asyncio.run(handlers.handle_get_method_card({}))
    assert "available" in out
    assert _KNOWN <= {c["signal_type"] for c in out["available"]}


def test_handler_unknown_signal_type() -> None:
    """未登録 signal_type は found=False＋一覧を添えて返す（誤解を誘わない）。"""
    out = asyncio.run(handlers.handle_get_method_card({"signal_type": "nope"}))
    assert out["found"] is False
    assert "available" in out
