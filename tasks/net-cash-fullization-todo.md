# TODO: ネットキャッシュ比率 JP の full 化（投資有価証券×70%）

作成: 2026-07-02 / 起票理由: ADR-079（清原式ネットキャッシュ・screen 拡張）の v1 は JP を**簡略式**で出したため、full 式化を直近 PR で行う。

## 背景
清原達郎式のネットキャッシュ比率:

    ネットキャッシュ = 流動資産 ＋（投資有価証券 × 70%）− 総負債
    比率 = ネットキャッシュ ÷ 時価総額

- **US（yfinance）は v1 から full 式**（`Investments And Advances` を投資有価証券として取得済み）。
- **JP（edinetdb.jp）は v1 で簡略式**＝`流動資産 − 総負債`（投資有価証券項を省略）。理由: edinetdb.jp に「投資有価証券」の専用フィールドが無い（近縁の `cross_shareholding_*` は政策保有株＝部分集合にすぎず過少）。簡略式は投資有価証券を切り捨てるぶん**保守的**（ネットキャッシュを過小評価）に倒れる。

## 直近 PR でやること（full 化）
1. **データ源**: 公式 EDINET（`edinet_api_key` 系統・`adapters/edinet.py`）の type=5 CSV ZIP（完全 XBRL）から標準タクソノミ要素 `jppfs_cor:InvestmentSecurities`（JP で唯一の投資有価証券専用要素）を抽出する経路を足す。
   - コスト大: XBRL 数値パース・連結/個別コンテキスト選択・IFRS 要素名差・提出日クロール型。本環境の `edinet_api_key` は未設定の疑い（config.py:183 が default 空）＝**鍵の用意も前提**。
2. **フォールバック設計**: edinetdb.jp（流動資産/総負債/現預金）＋ 公式 EDINET（投資有価証券）の合流。dossier 優先 2 段ガード（ADR-056）と同型で「公式 EDINET が取れた行だけ full 式に格上げ、取れなければ簡略式のまま」。
3. **quant**: `quant/valuation.py` の `net_cash(...)` は既に investment_securities 引数を持つ設計にしておく（v1 では JP は None を渡す）。full 化は「JP でも実値を渡す」だけで済むようにする。
4. **ADR**: ADR-079 の残課題を消し込み、full 化の判断を追記。

## 完了条件
- JP でも投資有価証券×70% を含む full 式で `net_cash_ratio` が焼かれる。
- 簡略式との差分（保守バイアス解消）が代表銘柄で確認できる。
