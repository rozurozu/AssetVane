# AI Advisor 設計（プロンプト・Tool・相談フロー）

AI Advisor を「専門家として機能する AI」にするための設計。
背景の決定は [decisions.md ADR-011/012/013/014/015](decisions.md)。

> **この製品の核心**: 「この銘柄どう思う？」と素の LLM に聞いても素人の感想しか返らない。専門性は「丁寧に質問すること」ではなく、**3 つの不変資産**に宿す。
> 1. **CORE プロンプト**（プロの規律・方法論）
> 2. **Tool ライブラリ**（実計算＝分析スキルのコード化）
> 3. **手法カード**（論文等のドメイン知識）
>
> `policy`（投資方針）はこの専門家を**操縦するハンドル**にすぎない。

---

## 0. 大原則：Python が事実、LLM が判断

LLM は計算しない。Python が計算した**構造化された事実**だけを **Tool Calling** で受け取り、解釈・方針づくり・提案を行う（[ADR-014](decisions.md)）。数値は必ず Tool の戻り値に紐づき、LLM が記憶や推測で数字を作ることを禁じる。

---

## 1. システムプロンプトの 2 層構造（CORE ＋ POLICY）

```
┌─ システムプロンプト ───────────────────────────────┐
│ 【CORE】不変・AIを"専門家"にする層（リポジトリ管理）   │
│ 【POLICY】可変・policyをコンパイルした層（DB管理）     │
└────────────────────────────────────────────────────┘
   ＋【手法カード】該当時（将来RAG）
   ＋【事実】毎回（Tool Calling の戻り値）
   ＋【文脈】直近の投資日記・対話履歴（連続性）
```

CORE と POLICY を**物理的に別の場所**に置くことで、専門性の核（CORE）がチャットで drift するのを防ぐ（[ADR-015](decisions.md)）。

| 層 | 置き場所 | 変更方法 |
|---|---|---|
| CORE | `backend/app/advisor/core_prompt.md`（jj 版管理）| 意図的なコミットのみ。チャット AI は触れない |
| POLICY | DB `policy` テーブル | チャットで気軽に育つ |

---

## 2. CORE の 5 要素（AI を専門家にする中身）

`core_prompt.md` に書く内容。これが「素人 AI」と「専門家 AI」を分ける。

| # | 要素 | 例 |
|---|---|---|
| ① | **役割** | 「あなたは規律あるクオンツ投資アナリストである。ニュースの寄せ集めや一般論で語らず、**与えられた定量データのみ**で判断する」 |
| ② | **方法論（"スキル"）** | 「モメンタム判定は移動平均・出来高・RSI を必ず併用」「ポートフォリオ提案時は相関・シャープレシオ・最大ドローダウンを必ず確認」「割安判断は PER 単体禁止、成長率と併せて見る」「**業績は『質』を見る**＝利益の伸びが売上/利益率/株数・一時要因のどれ由来か切り分け、直近の訂正報告があれば数字を割り引く＝[ADR-063](decisions.md)」 |
| ③ | **規律・ガードレール** | 「**数値は Tool の戻り値のみ**を使う。記憶・推測で数字を作らない」「不確実なことは不確実と明示」「トレードオフを必ず提示」「断定を避ける」 |
| ④ | **Tool の使い方** | 「定量的な主張をする前に、必ず対応する Tool を呼ぶ」 |
| ⑤ | **出力の型** | 「結論には必ず『どのデータ・どの手法から導いたか』の根拠と、想定リスクを添える」「買い/売り提案は **Bull/Base/Bear を定性的に併記**・**catalyst を名指し**・**前提崩れ条件（invalidation）を明記**・**確信度を高/中/低で明示**（確率の数値は作らない）＝[ADR-063](decisions.md)」 |

> tmp2 の「分析手法をスキルに」は 3 つに分解される: **計算そのもの＝Python関数（Tool）/ 手法の使い方・判断の作法＝CORE ②/ ドメイン知識＝手法カード**。

> **反追従＋ペルソナ層（[ADR-041](decisions.md)・⚠ 未実装）**: 規律③に**反追従**（ユーザーの誤った前提・未開示リスクに同意しない）を明示し、CORE 先頭に一人称の**ペルソナ／職業哲学**セクションを置く**計画**（ADR-041 は設計宣言のみで、現状の `core_prompt.md` には未反映。手法カード `cards/jp-market-context.md` がこれを既存前提で参照している点も実装時に整合させる＝`tasks/review-2026-06-12.md`）。固定するのは職業アイデンティティのみで、リスク選好（risk_tolerance/no_leverage 等）は POLICY のままユーザー可変（[ADR-013](decisions.md)/[ADR-015](decisions.md) の境界を侵さない）。反追従は事実・リスクに向かい、正当な POLICY 選好には向かわない（敬意を保ち事実で反論）。

---

## 3. POLICY のコンパイル

DB の `policy`（[data-model.md](data-model.md)）を、システムプロンプトの POLICY 層へ整形して差し込む。

- 構造化コア（`risk_tolerance` / `time_horizon` / `target_cash_ratio` / `max_position_weight` / `sector_caps` / `target_return` / `no_leverage` / `exclusions`）→ 判断の制約・志向として文章化。
- `rationale`（自由文）→ ニュアンス・理念として差し込む。
- 同じ構造化コアは **PyPortfolioOpt の制約**にも使われる（プロンプトと最適化で二重活用）。

例（ユーザーの相談を反映した policy のコンパイル結果）:
> 「リスク許容度は高め・短〜中期。リターンを大きく狙う。ただし**信用取引・レバレッジは禁止**（個別銘柄の全損は受容するが借金は負わない）。ポートフォリオ全体の大損は避けるため現金バッファと 1 銘柄上限を尊重する。攻めるが退場はしない。」

---

## 4. Tool Calling — AI が叩ける関数（事実取得）

AI は計算せず、これらの戻り値（Python が計算した事実）だけで議論する。Phase の進行に合わせて増える。

| Tool（例） | 返すもの | 投入フェーズ |
|---|---|---|
| `screen_stocks(criteria)` | 条件に合う上位銘柄＋指標値 | 1〜 |
| `get_indicators(code)` | テクニカル指標（移動平均/RSI/出来高 等）| 1〜 |
| `get_portfolio_metrics(portfolio_id)` | 相関・シャープ・最大ドローダウン | 2〜 |
| `optimize_portfolio(portfolio_id)` | policy 制約下の最適比率 | 2〜 |
| `get_financials(code)` | 決算数値（生の財務時系列）| 2〜 |
| `get_valuation(code)` | バリュエーション/ファンダ事実（PER/PBR/ROE/利益率/配当利回り/YoY 成長率＋業種内ランク＋会社予想の達成率〔`op/profit_forecast_achievement`＝beat/miss〕・上方/下方修正〔`op/profit_forecast_revision`〕＋`last_restatement_at`〔最新の訂正有報・提出日／無ければ null〕＋売掛/在庫の質〔`receivables/inventory_turnover_days`＝DSO/DIO・`receivables/inventory_growth_yoy`＝[ADR-064](decisions.md) #2・edinetdb.jp 源・null あり〕・market:JP 明示・verdict なし＝[ADR-048](decisions.md)/[ADR-063](decisions.md) #4）| 2〜 |
| `screen_valuation(criteria)` | バリュエーション条件で割安・優良候補を絞る（しきい値は AI がカード作法で渡す・[ADR-048](decisions.md)）| 2〜 |
| `get_asset_overview()` | 保有・現金・投信割合・資産推移 | 2〜 |
| `get_fund_holdings()` | 投資信託の保有・含み損益（基準価額ベースの随時計算・[ADR-054](decisions.md)）| 4〜 |
| `get_signals(date, type)` | 事前計算済みシグナル（momentum/volume_spike/stealth_accum は 1〜、ai_alpha は 5〜、lead_lag は 7〜）。`type='stealth_accum'`＝機関のステルス仕込み（[ADR-074](decisions.md)）| 1〜 |
| `submit_journal(...)` | 夜AI/チャットの所見を投資日記 `advisor_journal` に起票（書き込み系・チャット昇格は [ADR-029](decisions.md)）| 3〜 |
| `investigate_stock(code)` | 個別銘柄を調査しドシエを生成・更新（夜間バッチと共用のパイプライン）。調査中に**耐久的な知見（アノマリー・構造的な癖）**が出たらドシエでなく知識ノート向きなので `propose_card` を提案する（置き場所の逆提案・[ADR-062](decisions.md) 追補）| 4〜 |
| `get_dossier(code)` | 既存の銘柄ドシエ（調査レポート）を取得 | 4〜 |
| `get_news_context(code)` | 統合コーパス `news` から **(i) 銘柄／(ii) セクター／(iii) マーケットの 3 層を必ず構造的に揃えて**返す（読み取り専用・[ADR-044](decisions.md)）。銘柄層が空なら続けて `fetch_news`/`investigate_stock` を呼ぶ | 4〜 |
| `fetch_news(code, since)` | **今すぐネットから新規取得**して要約（本文は保持せず要約のみ）。`get_news_context` で銘柄層が薄いときの追加取得に使う | 4〜 |
| `get_general_news()` | 市況・マクロの一般ニュースだけを返す（`code` 不要・[ADR-034](decisions.md)）。銘柄の 3 層文脈が要るときは `get_news_context` を使う | 4〜 |
| `search_news(query, ...)` | 貯めた統合コーパスを**意味（embedding 余弦距離）で過去横断検索**（[ADR-045](decisions.md) 段階A・読み取り専用）。`level`/`code`/`sector17_code`/`since`/`until` で絞り込み可。embedding 未設定時は空＋理由（[ADR-018](decisions.md)）| 4〜 |
| `search_cards(query, level?, code?, market?, limit?)` | **知識ベース（知識カード）を検索**（[ADR-062](decisions.md)）。市場文脈・手法の解釈・登録した外部知識から今の話題に関係する**非自明な知識**を引く。`code` 指定は**その銘柄の active ノートを exact-match で全返し**（銘柄アノマリー等・意味クエリは無視・追補）。常時注入の ambient 以外の具体（銘柄/セクター）知識を掘るときに使う。embedding 未設定時は空＋理由 | 4〜 |
| `propose_card(body, title?, code?, market?, ...)` | 会話から得た**非自明な知識**を知識カードとして**承認制で起票**（[ADR-062](decisions.md) 追補）。後で再利用したい知識を残すときだけ呼ぶ。`code` を渡すと**特定銘柄のノート**（会話で論じている銘柄の code＝tool 文脈由来の grounded な値を・社名推測はしない・未知 code は drop・`level='stock'` 確定）。起票は draft で人間が `/cards` で active 化する。**壁打ちの作法**＝CORE 要素④の規律で「合意してから起票」（[ADR-065](decisions.md)）。内容が『今の事実・直近トピック・その銘柄の現況』なら知識ノートでなくドシエ向きなので `investigate_stock` を提案する（置き場所の逆提案・[ADR-062](decisions.md) 追補）。起票した draft の id は `ChatResponse.card_ids` で frontend に返り、チャット内に `/cards` 導線をインライン表示する | 4〜 |
| `adjust_card_weight(card_id, weight, reason)` | 既存カードの重要度 weight を変える提案を**承認制で起票**（[ADR-062](decisions.md) 追補）。古い/信頼度低を下げる等。`card_id` は `search_cards` の id・weight>0。`/proposals` で承認すると反映（削除せず生かす） | 4〜 |
| `get_lead_lag()` | 米国業種ショック → 翌営業日の日本業種スコア（リードラグ PCA）。業種ランキング＋検証メタ（IC・的中率・モデル基準日・プラン遅延）。提示専用（[ADR-009](decisions.md)/[ADR-039](decisions.md)）。軸1/2 共用 | 7〜 |
| `get_us_valuation(symbol)` | 米株のバリュエーション/ファンダ事実（`market:US`/`currency:USD` 明示＋売掛/在庫の質〔DSO/DIO・受取債権/在庫 YoY＝[ADR-064](decisions.md) #2・yfinance balance_sheet 源〕・verdict なし＝[ADR-055](decisions.md)）| 7〜 |
| `screen_us_valuation(criteria)` | 米株バリュエーション条件のスクリーニング（[ADR-055](decisions.md)）| 7〜 |
| `get_us_holdings()` | 米株の現在保有（USD/JPY 両評価・含み損益・[ADR-057](decisions.md)）| 7〜 |
| `list_themes()` | テーマ語彙の一覧（各テーマの銘柄数＋`near_duplicate_of` フラグ）。AI が有効なテーマ文字列を**当て推量せず discovery** する（[ADR-050](decisions.md)）| 7〜 |
| `get_stock_themes(market, code)` | 指定銘柄のテーマ一覧（grounded 事前タグ・[ADR-050](decisions.md)）。`market` は `JP`/`US` | 7〜 |
| `screen_by_theme(theme, market?, sector17_code?, gics_sector?, limit?)` | そのテーマを持つ銘柄を列挙（テーマ株スクリーニング・[ADR-050](decisions.md)）。`market` で絞り込み可。業種絞りは **JP=`sector17_code`（S17）／US=`gics_sector`（GICS 英語ラベル）の別引数**＝二体系を 1 引数に混載しない（[ADR-053](decisions.md)）。戻り値は**テーマ所属の事実のみ**（バリュエーション数値は持たない＝[ADR-014](decisions.md)）| 7〜 |
| `propose_trade(action, code, reason)` | ニュース起点の買い/売りアイデアを **`proposals`（`kind=buy/sell`）へ承認制で起票**（提示専用・[ADR-052](decisions.md)/[ADR-009](decisions.md)）。`action`=`buy`/`sell`、`code`=JP 5 桁または US ティッカー、`reason`=根拠のみ。**株数・金額などの数値は出さない**（[ADR-014](decisions.md)）。検証 only（実起票は橋渡しが tool_runs から拾う）で、銘柄解決して `{ok, company_name, market}` を返す。未知コードは `{error}`。承認しても発注はしない | 4〜 |

> 関数名・引数は実装時に確定。重要なのは「LLM は計算せず Tool を呼ぶ」という規律（[ADR-014](decisions.md)）。

> **テーマ 3 Tool の使い方（[ADR-050](decisions.md)）**: `list_themes` で語彙を把握 → `get_stock_themes`/`screen_by_theme` で引く。**競合比較は専用 Tool を作らず合成**＝`get_stock_themes(market, code)` でその銘柄のテーマを取り、`screen_by_theme(theme, sector17_code=<同セクター>)`（US は `gics_sector=<同セクター>`）で「同テーマ∩近セクター」を出す。テーマは**実在テキストに grounded な定性タグ**で、銘柄の `code`/`symbol`（同一性）に紐づく（名前推測由来でない・数値スコアでない）。

> **ニュース 3 Tool の役割分担**: `get_news_context(code)`＝直近窓の **(i) 銘柄／(ii) セクター／(iii) マーケットの 3 層を構造的に揃える**（直近の文脈）。`search_news(query, ...)`＝貯めた統合コーパスを**意味で過去横断検索**（lookback 窓を越えて遡る・[ADR-045](decisions.md)）。`fetch_news(code, since)`＝**今すぐネットから新規取得**（コーパスに無い最新を補う）。まず `get_news_context`/`search_news` で在庫を引き、足りないときに `fetch_news` で取りに行く。

> **売買アイデアの起票（[ADR-052](decisions.md)）**: `get_news_context`/`search_news` で根拠（3 層文脈）を掴んだうえで、**強い買い/売り材料があるときだけ** `propose_trade` で起票する（無ければ呼ばない＝毎回出さない）。提示専用ゆえ**方向と根拠のみ**で、株数・金額・目標価格は出さない（サイズは別途 `optimize_portfolio` の事実計算に委ねる＝[ADR-014](decisions.md)）。夜AI（軸1）・チャット（軸2）の両方から起票でき、起票された提案は `/proposals` 画面で承認/却下する（承認しても発注はしない＝約定後に手入力）。同一 `(kind, code)` の pending は重複起票しない（buy が pending でも同銘柄の sell は起票できる。reject/approve 後は再提案可＝[ADR-052](decisions.md)）。

> **知識ノート化の壁打ち（[ADR-065](decisions.md)）**: 会話の中で「残す価値のある非自明な知識」（市場の見方・外部メモ・手法の解釈など）が出たら知識カードにできるが、**黙って起票しない**。要点を一度要約し「この内容で知識ノートにしていい？」と一言確認し、ユーザーが合意してから `propose_card` を呼ぶ（乱発しない・一般教科書知識はカードにしない）。これは CORE 要素④の散文規律として常時効く（[ADR-062](decisions.md)「規律は CORE へ吸収」）。起票は draft 止まりで、active 化（本番助言に効かせる）は人間が `/cards` で行う（[ADR-009](decisions.md)）。チャット UI は `ChatResponse.card_ids` を読んで「🗂 下書き起票 → `/cards` で確認・承認」をインライン表示する。専用大画面ページ `/advisor`（[ADR-065](decisions.md)）でもフローティングでも同じ会話・同じ挙動。
>
> **置き場所の逆提案（ドシエ vs 知識ノート・[ADR-062](decisions.md) 追補）**: 銘柄に紐づくテキストは **ドシエ（`stock_dossiers`・今の事実/現況・`investigate_stock` が毎晩上書き）** と **知識ノート（耐久的な解釈/アノマリー・蓄積・承認制）** の 2 か所に置ける。ユーザーがどちらかに入れようとして内容が明らかに逆向きなら、AI は**書く前に理由を添えて置き場所を提案する**（両方向＝ノート依頼が今の事実なら `investigate_stock` を／調査中に耐久的知見が出たら `propose_card` を提案）。ただし**明らかに逆のときだけの弱いナッジ**で、境界的・ユーザーが意図して選んでいるなら従う（乱発しない）。`investigate_stock` は取得コストがかかるので勝手に走らせず必ず確認してから呼ぶ。判定は LLM の解釈に委ね handler は無改変（[ADR-014](decisions.md)）。

---

## 5. 手法の扱い（実装＝コード / 手法DB＝索引 / 参照知識）

**大前提（[ADR-016](decisions.md)）**: 計算・ロジックを持つ手法（一目均衡表のシグナル、モメンタム、出来高急増、リードラグ PCA など）は、**必ずテスト済みのコードとして実装する**。LLM にその場でコードを書かせて計算させる方式は、再現性が無く（毎回コードが変わり backtest が無意味）、細かな定義を黙って間違え、4000 銘柄の事前計算もできないため、**信頼できる手法の基盤にはしない**。

手法は 3 層に分けて扱う。

| 層 | 中身 | 役割 |
|---|---|---|
| **① 実装（コード）** | 一目均衡表・モメンタム・出来高急増・リードラグ PCA 等を**テスト済み Python** で実装。`signals` に事前計算 | **計算の真実。ここが全て** |
| **② 手法カタログ／索引（＝"手法DB"）** | ①の各手法のメタデータ（何をする手法か・いつ使うか・パラメータ・対応 signal・出典） | AI が「この状況ならどの手法を使うか」を**選ぶ索引**。**計算はしない** |
| **③ 参照知識（prose・計算なし）** | 計算を持たない定性知識（論文の所見・相場局面メモ・ニュース要約） | AI が**読んで判断材料**にする |

> **重要**: ②手法DB は「①コード化された手法への索引」であって、**コードの代わりにはならない**。RAG が効くのは主に②の手法選択と③の参照知識。

### 5.1 ②カタログはコードのメタデータから自動生成する

各手法モジュールが自分のメタデータ（名前・要約・適用条件・対応 signal・出典）を宣言し、レジストリがそれを集めて②カタログを作る。こうすれば**「説明」と「実コード」がズレない**（単一の真実）。embedding 索引（将来）もこのレジストリから作る。

> ただし**初期は手法が数個なので、ベタ書きの list で十分**。メタデータ宣言＋レジストリ自動生成の仕組み化は、手法が増えてから入れればよい（過剰実装を避ける）。

### 5.2 置き場所と RAG の段階

| 対象 | 初期 | 将来（手法・知識が増えたら）|
|---|---|---|
| ②手法カタログ | コードのレジストリ（全手法をプロンプトに列挙）| `method_cards` テーブル＋ embedding（`sqlite-vec`）で意味検索 |
| ③参照知識 | リポジトリの markdown（jj 版管理・不変資産扱い）| 同上テーブルへ取り込み RAG 化 |

**最初から DB / vector は作らない**。手法が数個なら全列挙で「どれを使うか」は選べる。vectorDB が効くのは手法・知識が大量になってから。将来の `method_cards` テーブルの形は [data-model.md](data-model.md) に「予約」として記載してある。

### 5.3 その場コード生成の位置づけ（試作専用）

チャットでの**使い捨ての探索的分析**（ユーザーが目視で確認するだけのアドホックな集計）に限り、サンドボックスの code 実行ツールを逃げ道として使ってよい。ただし**気に入った手法は必ず①のテスト済みコードに昇格させる**。本番の信頼できる手法をその場生成で回すのは禁止。

### 5.4 実装済み手法カタログ

①コード化済みの手法と、その参照知識（③＝論文要約等のリポジトリ markdown）の対応。

| 手法 | 実装（①コード）| 対応 signal | 投入 | 参照知識（③）|
|---|---|---|---|---|
| モメンタム | `quant/momentum.py` | `momentum` | 1〜 | — |
| 出来高急増 | `quant/volume_spike.py` | `volume_spike` | 1〜 | — |
| **業種リードラグ PCA** | `quant/lead_lag.py`（`compute_lead_lag`／`validate_lead_lag`）| `lead_lag` | 7〜 | [docs/methods/lead-lag.md](methods/lead-lag.md)（論文 SIG-FIN-036 の要約・参照知識・計算なし）|

> 業種リードラグ PCA は、米国業種 ETF の当日ショックを事前部分空間へ正則化した PCA で翌営業日の日本業種スコアに写す低ランク予測器（[ADR-039](decisions.md)）。AI は `get_lead_lag()` Tool で**算出済みの事実**（ランキング＋IC/的中率）だけを受け取り、解釈・提示に使う（数値は LLM が作らない＝[ADR-014](decisions.md)）。提示専用（[ADR-009](decisions.md)）。手法カード本体（仮説・ユニバース・手順・パラメータ・出典・留意）は③のリポジトリ markdown に置く（CLAUDE.md の手法カード方針）。

---

## 6. プロンプト組み立て（相談 1 ターンの構造）

```
[CORE]          ← core_prompt.md（不変）
[POLICY]        ← policy をコンパイル（可変）
[手法カード]     ← 該当時（手動 or RAG）
[事実]          ← Tool Calling の戻り値（Python 計算）
[文脈]          ← 直近の投資日記・対話履歴（連続性）
[画面コンテキスト] ← 軸2のみ。見ているページ＋主対象（ヒント・数値は含まない）
[ユーザー発話]    ← 「短期で大きく狙える銘柄を相談したい」「これ調査して」等
```

> **出力面ごとの差分は ChannelProfile に集約（[ADR-042](decisions.md)・⚠ 未実装）**: 軸1（nightly）と軸2（chat）の差（行動規範・出力形式・nightly 固有 instruction＝submit_journal 強制・画面コンテキスト有無）は、ベタ書き分岐ではなく `ChannelProfile`（コード定数 registry）に一元化する**計画**（ADR-042 は設計宣言のみで、現状実装は `nightly.py` のベタ書き instruction＋`source` 文字列分岐のまま）。**CORE／ペルソナは全プロファイル横断で不変**で、プロファイルは振る舞い・形式のみ差し替える（[ADR-011](decisions.md)「1 つの脳・複数の起動口」のプロンプト層版）。provider／model 選択は面別に DB＋WebUI（`services/llm_config.resolve_face`・`/settings`・[ADR-058](decisions.md)。旧 `config.provider_for` を置換）で持つ。

### 6.1 画面コンテキストの注入（指示語の解決）

相談チャットAI（軸2）は全ページ常駐で、ユーザーは**画面を見ながら**相談する（[ADR-024](decisions.md)・[screens.md §4](screens.md)）。「**これ**調査して」「**この**集中度どう？」のような**指示語**を解決するため、チャットのリクエストに「ユーザーが今見ているもの」を**軽量に**渡す（[ADR-025](decisions.md)）。

- **粒度**: 「ページ＋主対象」だけ。構造は `page` ＋ 任意の `focus`。
  ```
  page: stock_detail
  focus: { type: "stock", code: "6920" }   # 対象が無いページは focus 省略
  ```
- **プロンプトには 1 行の自然文にコンパイル**して差す（例: 「銘柄 6920 の詳細ページを見ている」）。
- **数値・画面データは渡さない**。画面コンテキストは「何の話をしているか」のヒントにすぎない。AI は数値が必要なら該当 Tool（`get_signals(6920)` 等）を呼んで**事実を取り直す**（＝[ADR-014](decisions.md) と整合。生データを丸投げしない）。
- **揮発情報で DB には保存しない**。送信時のみ使う。

> これで「画面の数字を見ながら、指示語で気軽に相談」が成立しつつ、トークン肥大と数値丸投げを避けられる。詳細は [ADR-025](decisions.md)。

---

## 7. 2 軸での使われ方

同じ組み立てを、起動契機だけ変えて 2 軸で使う（[architecture.md 2.2](architecture.md)）。

- **軸1 夜の分析AI（cron）**: 上記を自動実行し、「昨日までの方針」と「今日の事実」を突き合わせ → **方針見直し提案＋投資日記** を生成。方針変更は承認制。**画面が無いので画面コンテキストは持たない**。
- **軸2 相談チャットAI（対話）**: ユーザー発話ごとに組み立て → 応答。**全ページ常駐**（[ADR-024](decisions.md)）で、**画面コンテキスト**（§6.1）を受け取り「これ／この銘柄」等の指示語を解決する。合意に至れば `policy` を更新（その変更を日記にスナップショット）。

---

## 8. 相談フローの具体例

ユーザーが「短期で大きく狙える方針を相談したい」と入力した場合:

1. **CORE** をセット（規律あるアナリスト）。
2. **POLICY** をコンパイルして差し込む（現在の方針）。
3. **Tool 実行**: `screen_stocks` で条件に合う上位銘柄と数値を取得、`get_portfolio_metrics` で現状リスクを取得。
4. 必要なら**手法カード**（該当する手法の要約）を差し込む。
5. **LLM が提案**: 「データと○○手法を照らすと A 社が候補。根拠は出来高急増パターン。ただし集中投資のため、現在の policy の『マイナス回避』とトレードオフ。1 銘柄上限を一時的に上げるか確認したい。レバレッジは使わない前提（ゼロカット許容）」のように、**根拠とリスクとトレードオフを明示**して提案。
6. 合意したら `policy` を更新し、日記にスナップショットを残す。

> ポイント: 「人間が仕込んだ分析スキル（CORE＋Tool＋手法カード）」を、AI が**優秀な相談役として噛み砕いて提示**する。AI は数字を作らず、規律に従って判断する。

---

## 9. 実装フェーズ

この設計の実体は [roadmap.md Phase 3](roadmap.md) で構築する。Phase 4（銘柄ドシエ）・Phase 5（決算スコア）・Phase 7（リードラグ）が進むほど、Tool と手法カードが増え、Advisor が賢くなる。
