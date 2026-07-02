"""手法カードのローダ — advisor/method_cards/<key>.md を起動時に読む（ADR-075・ADR-079 で一般化）。

設計の真実: docs/decisions.md ADR-075（手法カードはリポジトリ所有）・ADR-079（kind で一般化）。

- **リポジトリ所有の第 4 知識源**（CORE／POLICY／knowledge_cards に続く）。アプリ/AI からは
  追加・編集できない（手法追加はコード変更を伴う）＝git・code review で入れる（ADR-015 同型）。
- 各 md は frontmatter（kind / signal_type or slug / summary）＋本文。起動時に 1 度 dict へ読む。
- **kind は 2 種**（ADR-079）:
  - `signal`（既定）＝signals に焼く signal_type の解釈。名＝signal_type・ドリフト検査対象。
  - `strategy`＝signal を持たない手法（例: 清原式ネットキャッシュの screen 運用）。ファイル名＝手法
    スラッグ。signal と 1:1 対応しないのでドリフト検査（orphan/missing）の対象外。
- 注入は skill 型 progressive disclosure＝get_method_card の description に summary を常時露出し、
  本文は get_method_card(key) を呼んだ時だけ返す（決定論注入はしない＝ADR-062）。
- ドリフト検査 validate_method_cards は **signal 種のみ**「登録 signal_type とファイル」の
  書き忘れ/孤児を検出（strategy 種は signal に紐づかないので除外＝ADR-079）。
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
    """method_cards/*.md を読み {key: {signal_type, kind, summary, body}} を返す（ADR-079）。

    key は signal 種＝signal_type、strategy 種＝ファイル名スラッグ（frontmatter に signal_type
    が無ければ path.stem を使う）。後方互換のため辞書フィールド名は "signal_type" のまま
    （signal 種は signal_type・strategy 種はスラッグを保持）。kind は既定 "signal"。
    """
    cards: dict[str, dict[str, str]] = {}
    if not _CARDS_DIR.is_dir():
        return cards
    for path in sorted(_CARDS_DIR.glob("*.md")):
        meta, body = _parse_card(path.read_text(encoding="utf-8"))
        key = meta.get("signal_type") or path.stem
        cards[key] = {
            "signal_type": key,
            "kind": meta.get("kind", "signal"),
            "summary": meta.get("summary", ""),
            "body": body,
        }
    return cards


# 起動時に 1 度だけ読む（CORE と同じ・チャット/夜AI で書き換えない）。
_CARDS = _load()


def method_card_index() -> list[dict[str, str]]:
    """全カードの {signal_type, kind, summary} を signal_type 昇順で返す（Tool カタログ用）。"""
    return [
        {"signal_type": c["signal_type"], "kind": c.get("kind", "signal"), "summary": c["summary"]}
        for c in sorted(_CARDS.values(), key=lambda c: c["signal_type"])
    ]


def get_method_card(signal_type: str) -> dict[str, str] | None:
    """1 手法カードの {signal_type, summary, body} を返す。未登録は None。"""
    return _CARDS.get(signal_type)


def catalog_text() -> str:
    """Tool description に常時露出するカタログ（複数行・ADR-075/079）。

    signal 種は `- key: summary`、strategy 種は `- key [strategy]: summary`（signal を見た流れで
    引く手法か、能動的に screen で使う手法かを LLM が 1 行で見分けられるように）。
    """
    lines = []
    for c in method_card_index():
        tag = " [strategy]" if c.get("kind") == "strategy" else ""
        lines.append(f"- {c['signal_type']}{tag}: {c['summary']}")
    return "\n".join(lines)


def validate_method_cards(known_signal_types: set[str]) -> dict[str, list[str]]:
    """ドリフト検査（signal 種のみ・ADR-079）。known にカード無し(missing)／カードが known に無い
    (orphan) を返す。strategy 種は signal に紐づかないので対象外（orphan 誤検出を防ぐ）。
    """
    have_signal = {k for k, c in _CARDS.items() if c.get("kind", "signal") == "signal"}
    return {
        "missing": sorted(known_signal_types - have_signal),
        "orphan": sorted(have_signal - known_signal_types),
    }
