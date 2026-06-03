"""services パッケージ — DB とルータの間に挟まる薄い糊ロジック層。

- holdings.py  … transactions から holdings を再計算（ADR-019）。
- policy.py    … policy テーブルの有無を吸収し既定値を返す（Phase 3 まで）。
- portfolio.py … 評価額計算・price_panel 構築など quant 呼び出し前の下ごしらえ。
"""

from __future__ import annotations
