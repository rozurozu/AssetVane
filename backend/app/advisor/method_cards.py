"""手法カードのローダ — advisor/method_cards/<signal_type>.md を起動時に読む（ADR-075）。

設計の真実: docs/decisions.md ADR-075（手法カードはリポジトリ所有・signal_type キー）。

- **リポジトリ所有の第 4 知識源**（CORE／POLICY／knowledge_cards に続く）。アプリ/AI からは
  追加・編集できない（手法追加はコード変更を伴う）＝git・code review で入れる（ADR-015 同型）。
- 各 md は frontmatter（signal_type / summary）＋本文。起動時に 1 度だけ dict へ読む。
- 注入は skill 型 progressive disclosure＝get_method_card の description に summary を常時露出し、
  本文は get_method_card(signal_type) を呼んだ時だけ返す（決定論注入はしない＝ADR-062）。
- ドリフト検査 validate_method_cards で「登録 signal_type とファイル」の書き忘れ/孤児を検出。
"""

from __future__ import annotations

from pathlib import Path

_CARDS_DIR = Path(__file__).parent / "method_cards"


def _parse_card(text: str) -> tuple[dict[str, str], str]:
    """先頭の `---` frontmatter（`key: value` の 1 行群）を dict に、残りを本文で返す。

    PyYAML を足さず単純な key: value だけ読む（値は 1 行・ADR-075 のカードはこの範囲で足りる）。
    frontmatter が無ければ ({}, 全文) を返す。
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text.strip()
    meta: dict[str, str] = {}
    body_start = len(lines)
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            body_start = i + 1
            break
        key, sep, value = lines[i].partition(":")
        if sep:
            meta[key.strip()] = value.strip()
    return meta, "\n".join(lines[body_start:]).strip()


def _load() -> dict[str, dict[str, str]]:
    """method_cards/*.md を読み {signal_type: {signal_type, summary, body}} を返す。"""
    cards: dict[str, dict[str, str]] = {}
    if not _CARDS_DIR.is_dir():
        return cards
    for path in sorted(_CARDS_DIR.glob("*.md")):
        meta, body = _parse_card(path.read_text(encoding="utf-8"))
        signal_type = meta.get("signal_type") or path.stem
        cards[signal_type] = {
            "signal_type": signal_type,
            "summary": meta.get("summary", ""),
            "body": body,
        }
    return cards


# 起動時に 1 度だけ読む（CORE と同じ・チャット/夜AI で書き換えない）。
_CARDS = _load()


def method_card_index() -> list[dict[str, str]]:
    """全カードの {signal_type, summary} を signal_type 昇順で返す（Tool カタログ用）。"""
    return [
        {"signal_type": c["signal_type"], "summary": c["summary"]}
        for c in sorted(_CARDS.values(), key=lambda c: c["signal_type"])
    ]


def get_method_card(signal_type: str) -> dict[str, str] | None:
    """1 手法カードの {signal_type, summary, body} を返す。未登録は None。"""
    return _CARDS.get(signal_type)


def catalog_text() -> str:
    """Tool description に常時露出するカタログ（`- signal_type: summary` の複数行・ADR-075）。"""
    return "\n".join(f"- {c['signal_type']}: {c['summary']}" for c in method_card_index())


def validate_method_cards(known_signal_types: set[str]) -> dict[str, list[str]]:
    """ドリフト検査。known にカード無し(missing)／カードが known に無い(orphan) を返す。"""
    have = set(_CARDS)
    return {
        "missing": sorted(known_signal_types - have),
        "orphan": sorted(have - known_signal_types),
    }
