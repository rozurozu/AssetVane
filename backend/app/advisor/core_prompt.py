"""CORE プロンプトのロード（ADR-015）。

設計の真実: docs/decisions.md ADR-015（システムプロンプトは「不変 CORE＝リポジトリの prompt
ファイル」＋「可変 POLICY＝DB の policy」に分離）。

CORE は同居する core_prompt.md（リポジトリ管理・意図的なコミットでしか変わらない）。プロセス
起動時に 1 度だけ読み、router（軸2 チャット）と nightly（軸1 夜AI）の双方がここから import する
（method_cards.py と同じ「静的参照データのロード」流儀）。以前は router の private `_CORE` を
nightly が import していた＝HTTP ルータへの逆流だったため、中立な本モジュールへ寄せた
（tasks/review-2026-06-12.md §3）。
"""

from __future__ import annotations

from pathlib import Path

# 起動時に1度だけ読む（チャットでは書き換えない・ADR-015）。
CORE = (Path(__file__).parent / "core_prompt.md").read_text(encoding="utf-8")
