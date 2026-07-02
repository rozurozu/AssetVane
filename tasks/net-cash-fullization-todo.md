# DONE: ネットキャッシュ比率 JP の full 化（投資有価証券×70%）

作成: 2026-07-02 / 完了: 2026-07-02（ADR-079 追補）

## 結論（当初前提の誤りを上書き）

当初は「edinetdb.jp に投資有価証券の専用フィールドが無い → 公式 EDINET の完全 XBRL（`jppfs_cor:InvestmentSecurities`）を
別 PR で抽出する」計画だった。**この前提は誤りだった**。

裏取りの結果、edinetdb.jp の OpenAPI（`https://edinetdb.jp/v1/openapi.yaml`・147 フィールド）に
**`investment_securities`（投資有価証券・円）が実在**していた（ほか `short_term_securities`・
`investments_and_other_assets` もある）。現アダプタ `adapters/edinetdb._normalize_financial` が
そのキーを読んでいなかっただけ。

よって **公式 EDINET の XBRL パーサも 401 疑いの公式キーも一切不要**で、`calc_receivables_inventory` が
既に叩く**同じ `/financials` レスポンス**から 1 キー抽出するだけで JP もフル式になった（追加フェッチ ゼロ）。

## 実体（達成内容）

- 写像は **`investment_securities`（非流動）のみ ×0.7**。`short_term_securities`（有価証券・流動）は既に
  `current_assets` に含まれ二重計上になるため足さない。`investments_and_other_assets`（親項目・長期貸付金/
  敷金/繰延税金資産を含む）は過大評価になるため使わない（US の `Investments And Advances`＝非流動と対称）。
- 欠落（IFRS 銘柄・古い年）時のみ簡略式（流動資産 − 総負債・保守側）に自然フォールバック。
- コード変更は `adapters/edinetdb._normalize_financial` に `investment_securities` を追加（1 行）＋
  docstring/コメント更新のみ。quant/service/物理列 `net_cash`/`net_cash_ratio` の read-time 導出/screen/Tool は
  既にフル式対応済みで無改変。migration 不要（`net_cash` 列は 0038 で既存）。
- ATDD: `tests/test_edinetdb.py`（正規化で投資有価証券を写像・欠落は None）＋
  `tests/test_edinetdb_quality.py`（フル式/欠落フォールバック）で先行検証。

## 運用時に残る確認（コード完了・実データは次回運用で裏取り）

- 実データ焼き込み差分（代表銘柄で簡略式→フル式）と単位裏取りは dev 実機プローブで確認する
  （`build_edinetdb_adapter` で `/financials` を 1 回叩き、`investment_securities` が当期 annual 行に入り
  `current_assets`/`total_liabilities` と同一単位＝円であること）。取れなければ None → 簡略式で安全。
