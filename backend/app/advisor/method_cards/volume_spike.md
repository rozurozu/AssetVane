---
signal_type: volume_spike
summary: 出来高急増（過去20営業日平均比）。単日の ratio が高いほど「何か起きている」目印。教科書。
native_horizon: short（当日〜数日の需給イベント）
---
# volume_spike — 出来高急増

当日出来高 ÷ 過去20営業日平均（当日除く）の比 `ratio` を score 化する。低流動性銘柄は除外。

## スコアの読み方
- payload `ratio`（例 3.2 = 平常の 3.2 倍）。高いほど異常。
- `notable` は ratio≥3.0 の目印。ratio≥7 は極増で、notable では単独でも候補になる（ADR-067）。

## 限界・注意
- 単日の急増で、方向（買い/売り）は示さない。値動き・ニュースと併せて解釈する。
- 「持続的な出来高増 × 価格圧縮」は別現象＝そちらは stealth_accum を見る。

計算の真実は `quant/volume_spike.py`。
