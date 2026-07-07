---
signal_type: ai_alpha
summary: AI決算スコア（LightGBMが予測する対TOPIX 60日超過リターンの、当日ユニバース内パーセンタイル順位0..1）。
native_horizon: medium（対TOPIX 60営業日≈3ヶ月の学習ターゲット）
---
# ai_alpha — AI Alpha Scorer（AI決算スコア）

財務・決算データを学習した LightGBM が「今後60営業日の対 TOPIX 超過リターン」を予測し、その予測値を**当日のユニバース内でパーセンタイル順位（0..1）に正規化**したもの。label「AI決算スコア」は簡略表示で、実体は超過リターン予測の相対順位。

## スコアの読み方
- score 0..1 は**当日の全銘柄内での相対順位**（絶対的な良し悪しではない）。1 に近い＝今日のユニバースで最も超過リターンが見込まれる側。
- payload `predicted_excess_return_60d`（生の予測超過リターン）、`model_version`、`feature_snapshot`（予測根拠の特徴量）。生値の符号・大きさも併せて見る。

## 限界・注意
- **予測であって事実ではない**。walk-forward CV の IC は約 0.08（弱いエッジ・外れも多い）。単独で売買判断しない。
- モデルは別 PC で学習した `.pkl` の推論で、**古くなりうる**（`model_version`・鮮度を確認）。.pkl 未配置なら signal は出ない。
- 「決算スコア」という語感だが「決算の良し悪し点」ではなく「超過リターン予測の相対順位」。

計算の真実は `quant/ml/`（推論 `infer.py`）、学習は別 PC（ADR-006/066）。
