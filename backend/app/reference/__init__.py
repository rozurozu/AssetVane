"""参照知識パッケージ（ADR-053・docs/decisions.md）。

IO 無し・副作用無し・標準ライブラリのみ依存の参照データ専用パッケージ。
コード表・ラベル・体系間マッピングといった「接続情報でも domain ロジックでもない
参照知識」を SSOT として置く。依存規約は「全レイヤ → reference は OK／
reference → 全レイヤは禁止」（config/logging_config と同じ中立横断・序列の最内）。
"""

from __future__ import annotations
