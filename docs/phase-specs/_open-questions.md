# 未解決質問（ユーザー裁定が必要な 9 件）

> Phase 1〜6 の着工仕様は、各項目に**推奨値を「既定」として採用済み**なので、このまま着工できる。
> ただし以下 9 件は **投資の好み・コスト・運用時間** に関わり、ユーザー本人の価値判断が要る。
> 既定値で進めて構わないが、違うなら着工前（または各 Phase 着手時）に差し替えてほしい。
> いずれも **後から env / `policy` / 設定で変えられる形** で実装する前提（コード作り直し不要）。
>
> 出所: `_review.md` F-1 / `_arbitration.md` 決定8。チーム合意済みの推奨値。

---

| # | 論点 | 既定値（spec 採用） | 代替案 | 影響 Phase | 後から変える容易さ |
|---|---|---|---|---|---|
| **U-1** ✅設計確定 | momentum の重み（連続スコア化＝ADR-026） | `W_TREND=0.6 / W_RSI=0.4`＋GC/反転は加点 | 等加重 / 重み調整 | 1 | 易（コード定数→method_settings・ADR-027） |
| **U-2** ✅設計確定 | volume_spike の notable 閾値・保存フロア | notable `ratio≥3.0`・保存 `ratio≥1.5`・score=min(ratio/10,1) | 閾値 2.0〜5.0・スケール変更 | 1 | 易（同上） |
| **U-3** ✅裁定済み | 無リスク金利 rf（シャープ計算） | **`RISK_FREE_RATE=0.0` 固定**。ADR-027 レーン（名前付き定数→将来 method_settings・policy/env には入れない） | policy/設定で可変化（日本国債利回り等） | 2 | 易 |
| **U-4** ✅裁定済み | P5 ML の予測ラベル | **60 営業日先の対 TOPIX 超過リターンを回帰**（中期ファンダ枠・分類化しない） | 20日 / 2値分類（上昇 or not） | 5 | 中（特徴量・学習やり直し） |
| **U-5** ✅裁定済み | LLM のコスト許容上限 | **$50/月・3 値トグル（off/warn/block）・既定 warn**・OpenRouter 実コスト計上・env 既定＋設定 UI 上書き（ADR-028） | モデル変更・呼び出し頻度調整 | 3 | 易（.env でモデル差替＝ADR-012） |
| **U-6** ✅裁定済み | 会話履歴の永続先 | **localStorage（生ログ・揮発でなく同一ブラウザで永続）＋重要点は承認付きで journal 昇格**（ADR-029・`advisor_journal.source`） | DB 保存（永続・検索可） | 3 | 中（保存層の追加） |
| **U-7** ✅裁定済み | チャットでの policy 更新 | 構造化コア=**承認制**（proposals 経由）/ `rationale`=即時反映。グローバルトグル無し・exclusions も承認制 | 全て即時 / 全て承認制 | 3 | 易 |
| **U-8** ✅裁定済み | 夜間ドシエの調査頻度・watchlist 上限 | **毎晩・古い順 N=3 件**・stale 閾値 21 日・watchlist 硬い上限なし・N は env＋設定 UI ツマミ（U-5 と同居） | N・頻度・閾値を変更 | 4 | 易（設定値） |
| **U-9** ✅裁定済み | 夜間 cron の起動時刻 | **02:00 JST**（TZ=Asia/Tokyo・大体寝ている帯） | 生活時間に合わせ変更 | 1, 3 | 易（cron 設定） |

---

> **U-1 / U-2 は grill 済みで設計方針が確定**（2026-06-03）。signals は連続スコアの「材料」で閾値は破壊的ゲートにしない（[ADR-026](../decisions.md)）／パラメータは Phase 1 はコード定数・env 不可・将来 `method_settings`＋UI＋AI 相談（[ADR-027](../decisions.md)）。表の「開始既定」は後でツマミ調整する前提の出発値で、このまま着工してよい。
>
> **U-3〜U-9 も grill 済みで裁定確定**（2026-06-03）。U-3=rf 0.0（ADR-027 レーン）／U-4=60 営業日超過リターン回帰／U-5=$50・3 値トグル・OpenRouter 実コスト計上（**新 [ADR-028](../decisions.md)**）／U-6=localStorage＋journal 昇格トリガー・`advisor_journal.source` 追加・文言修正（**新 [ADR-029](../decisions.md)**）／U-7=ハイブリッド維持（トグル無し）／U-8=毎晩 N=3・stale 21 日・硬い上限なし／U-9=02:00 JST。詳細は各 Phase spec の §OPEN に反映済み。

## 補足: 「裁定ではなく実機確認」が要る技術リスク（ユーザー判断は不要だが着工前に検証）

これらは好みではなく**事実確認**。Phase 1 着手の最初のゲートで潰す。

1. **依存ライブラリの ARM(aarch64) ビルド可否**（ADR-021）: numpy/pandas（Phase 1）、PyPortfolioOpt/cvxpy（Phase 2）、lightgbm（Phase 5）。Docker クロスビルドで検証（段取り=data-arch、依存選定=quant）。
2. **J-Quants V2 の未確認エンドポイント**（`jquants.md` 要再確認に追記済み）: `/v2/equities/master` の全件取得可否、V2 財務（statements）、取引日カレンダー API、主要指数 API の有無。
3. **Light プラン移行時期**（ADR-008「実運用時」）: Free は 12 週間遅延。短期シグナルを実弾運用する段になったら Light へ。これは U ではないが運用判断。

---

## 裁定済み（Phase 3 実機検証で発見・2026-06-04）

- **U-10 ✅裁定済み（①採用・2026-06-04）: `proposed_policy_change` が `{field,to}` 以外の dict（多フィールド patch 等）のときの扱い。**
  実機検証で 9B モデルが `{max_position_weight, sector_diversification_limit, target_cash_ratio}` のような**多フィールド patch** を出した。当時 nightly は「dict でありさえすれば proposal を起票」していたが、`apply_policy_change` は `{field, to}`（単一変更・[ADR-013](../decisions.md)）しか食えないため、**承認時に `ValueError` で落ちる「適用不能 proposal」が queue に入る**経路があった。真因は出力契約（JSON Schema）が `{field,to}` 形を構造で表明していなかったこと。
  選択肢: **(A) 単一 {field,to} を schema で構造強制**（field は policy 列の enum・required・他形は受理側で破棄）／**(B) 多フィールド patch を正式対応**（apply_policy_change と body を複数列 patch に拡張＝契約拡張）。
  **裁定: (A) を採用**（新 [ADR-030](../decisions.md)）。`ProposedPolicyChange` ネスト型＋`field` enum（`DEFAULT_POLICY` と一致・ドリフトガードテスト）で LLM に単一形を要求し、`coerce_policy_change` が不適合（多列 patch・非 dict・to 欠落・enum 外 field）を None に倒して適用不能 proposal を起票しない。schema は予防、coerce は [ADR-018](../decisions.md) の防御層として両立。多項目を直したい晩は提案を複数起票させる。`apply_policy_change` にも未知 field 防御を追加。なお「弱モデルが任意引数に壊れた値を渡す」一般クラス（非 dict の change／`"None"` 文字列の int 等）は `_ToolArgs` 正規化と `submit_journal` で握り済み（[ADR-018](../decisions.md)）。
