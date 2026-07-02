---
signal_type: momentum
summary: モメンタム（SMA25/75・Wilder RSI14）。教科書指標。GC=買い転換の遅行サイン、RSI反転=売られすぎ反発。
---
# momentum — モメンタム

SMA25/75 のゴールデンクロス（GC）・Wilder RSI(14) の売られすぎ反転・上昇トレンド継続を、連続スコア（0..1）で表す。score は「今クロスしたか」のイベント値でなく上昇トレンドの強度。

## スコアの読み方
- payload の `golden_cross` / `rsi_reversal` は当日イベント、`sma25`/`sma75`/`rsi14` は水準、`change_5d` は 5 日騰落率。GC 当日・RSI 反転当日は加点される。

## 限界・注意
- GC は遅行・高頻度で、単独では騙しが多い（notable では単独では拾わない＝ADR-067）。
- 教科書指標。単一指標で結論せず、移動平均・出来高・RSI を併用する（CORE の規律）。

計算の真実は `quant/momentum.py`。
