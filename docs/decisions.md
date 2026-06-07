# Design Decisions（設計判断の記録 / ADR）

「なぜそうしたか」を未来の自分が思い出すための記録。
各決定は、状況・決定・理由・代替案で構成する。

---

## ADR-001: 単一ユーザー前提で作る

- **状況**: ラズパイ上で動かす自分専用ツール。家庭内 LAN で使う。
- **決定**: ログイン認証を入れない。テーブルに `user_id` を持たせない。ただし「複数持てる器」（ポートフォリオ等）は ID を持たせ拡張余地を残す。
- **理由**: 利用者が自分 1 人なので認証・セキュリティ・公開デプロイが不要になり全体が大幅にシンプルになる。
- **代替案**: 複数ユーザー対応 → YAGNI。

## ADR-002: データベースは SQLite（WAL モード）

- **状況**: 扱うのは日足データ。数百万〜1000 万行程度。tick は扱わない。
- **決定**: SQLite を採用し WAL モードを有効化。
- **理由**: 単一ユーザー・読み中心なら十分。WAL で夜間バッチの書き込みと画面の読みが衝突しにくい。別プロセス（DB サーバー）不要でメモリ節約。
- **代替案**: PostgreSQL → 同時大量書き込みや tick 級になったら再検討。

## ADR-003: ラズパイ 4B でローカル完結、クラウド不使用（LLM 推論は例外的に外部）

- **状況**: 非リアルタイム（毎晩・毎月の定期処理）用途。
- **決定**: 計算・DB・配信はラズパイ 4B（8GB 推奨）に集約。Supabase / Vercel / AWS は使わない。**ただし LLM 推論だけはクラウド（OpenRouter）に出す**（[ADR-012](#adr-012-llm-はアダプタで抽象化openrouter-既定ローカルへ差替可)）。
- **理由**:
  - Supabase は単一ユーザーに過剰、Vercel は Python 常駐に不向き、AWS 無料枠は 1GB で非力かつ 12 か月の崖。
  - クラウド LLM を使えば**推論はクラウド側**で走るため、ラズパイは API を叩くだけで足り、低消費電力・常時起動の旨味を保てる。
  - 将来ローカル LLM を使うなら **Mac mini**（低消費電力・常時起動向き）を母艦にする。
- **代替案**: 全クラウド構成 → 今は不要。

## ADR-004: フロント=Next.js / バック=FastAPI（スタック A）

- **状況**: 利用者の既存スキルは Laravel / Vue。React / Next / Python は未経験。
- **決定**: Next.js（App Router）+ FastAPI を採用。
- **理由**:
  - バックエンドが Python なのは確定。PyPortfolioOpt / TA-Lib / LightGBM は PHP に無く、数理・ML がこの製品の心臓。
  - フロントは利用者が **React/Next を学ぶこと自体を目的に含めて自ら選択**した。
- **代替案**: Nuxt（Vue 既知）/ Streamlit（最速）→ 学習目的を優先して不採用。

## ADR-005: DB に触れるのは FastAPI のみ（Next は REST 経由）

- **状況**: README 初稿は Next(Prisma) と FastAPI が同じ SQLite を共有する構図だった。
- **決定**: DB に触れるのは FastAPI だけ。Next.js は REST API 経由。**Prisma は不採用。**
- **理由**: 書き手を Python 1 つに限定すれば SQLite のロック競合が原理的に起きない。スキーマも 1 箇所に集約。責務が明確になり学習時も混乱しない。
- **代替案**: README 通りの DB 共有 → 競合・二重管理の罠で不採用。

## ADR-006: 機械学習の「学習」は別 PC、ラズパイは推論のみ

- **状況**: LightGBM の学習（Phase 5）はラズパイには重い。
- **決定**: 学習は別 PC で行い、`.pkl` をラズパイにコピーして推論のみ。
- **理由**: 学習は低頻度・高負荷、推論は軽い。ラズパイ資源を推論・配信に集中。
- **代替案**: ラズパイで学習 → 非現実的。

## ADR-007: 通知は Discord Webhook（LINE Notify は不採用）

- **状況**: README 初稿は LINE Notify 前提。
- **決定**: 通知の主軸を Discord Webhook に。
- **理由**: **LINE Notify は 2025/3/31 で終了済み**。Discord Webhook は無料・登録不要・軽い。
- **代替案**: LINE Messaging API → 設定が重く後回し。

## ADR-008: J-Quants は Free プランで開発、運用時に有料へ／V2 を使う

- **状況**: J-Quants Free は株価が 12 週間遅延（約 2 年分）。また **2026/6/1 に V1 が終了し V2（API キー方式）へ移行**。
- **決定**: 開発は Free・**V2** で行う。短期機能の実運用時に Light 以上へ。
- **理由**: ロジックも DB スキーマもプラン非依存で、違うのはデータの鮮度だけ。課金後に作り直し不要。認証は V2 の `x-api-key` 方式（トークン更新自動化が不要に）。
- **代替案**: 完全無料に縛る → 短期機能の実運用が成立しないため運用時は有料前提。
- **補足（スロットル間隔はプラン名で持つ・2026-06-06）**: レート制限（Free 5 / Light 60 req/分）に対するスロットル間隔は、**env で秒数を直接お守りせず `JQUANTS_PLAN`（`free`/`light`）というプラン名 1 語で指定**し、秒数はコード内マッピング（`adapters/jquants.py` の `_PLAN_INTERVALS`＝`free`→16s / `light`→1s）が決める。**V2 に契約プランを返す API は無い**（公式 rate-limits・V2 エンドポイント一覧で確認・`X-RateLimit` ヘッダも無し）ため自動検出はできず、env でプラン名を渡す。`free`=16s は本番投入の実測根拠あり（[jquants.md §4](jquants.md)）、`light`=1s は目安（実運用で要実測）。`standard`/`premium` は未実測のため未収載（必要時に実測して足す）。未知プラン名は `free`（最も遅い＝最安全）に倒し warning を出す（typo で速くしすぎてブロックを誘発しない）。プラン移行は実運用時に env のこの 1 語を変えるだけ（秒数のコード変更は不要）。運用設定なので env を持つ（手法パラメータの [ADR-027](#adr-027-手法パラメータは-phase-1-はコード定数将来-method_settingsai-は助言自動改変しない) が env を挟まないとしたのとは別系統・[ADR-028](#adr-028-llm-コストガードレール監視と上限3-値トグルenv-既定設定-ui-上書き) と同じ）。

## ADR-009: 日米業種リードラグ戦略は AssetVane の分析機能とする（自動トレードツールに持ち込まない）

- **状況**: 部分空間正則化付き PCA による日米業種リードラグ戦略（SIG-FIN-036, 2026）を機能化したい。別途、自動トレードツール構想もある。
- **決定**: この戦略は AssetVane の機能（シグナル提示のみ）として実装。自動トレードツールには持ち込まない。
- **理由**: 戦略の本質は日次シグナルで、リアルタイム発注インフラ・tick を要求しない。「提示するだけ」哲学と一致。将来無人執行したくなったら同じシグナルを執行レイヤーが読めばよく、二重実装不要。
- **代替案**: 自動トレード側で実装 → 高速執行基盤は不要なため不採用。

## ADR-010: データソースは差し替え可能なアダプタ構成にする

- **状況**: 日本株は J-Quants だが、主要指数・米国株・FX は J-Quants 範囲外。
- **決定**: データ取得を共通インターフェースのアダプタ（`JQuantsAdapter` / `IndexAdapter` / `UsEquityAdapter` / `FxAdapter`）で抽象化する。
- **理由**: データ元が複数になる。アダプタ化すれば提供元が変わっても上位ロジックを書き換えずに済む。
- **補足**: スコープは「日米株が主役、投信・現金・指数はマクロ文脈として軽く扱う」。投信（オルカン等）NAV は正確な無料ソースが不確実なため、当面は「全体に対する割合」程度の手入力＋proxy 指数で扱い、深追いしない。
- **代替案**: J-Quants 直結のハードコード → 米株・指数を足せず詰む。

## ADR-011: AI Advisor を 2 軸（夜の分析AI＋相談チャットAI）で実装する（製品の核心）

- **状況**: 数理計算の結果を見せるだけでは「で、どうすべきか」が分からない。AI に投資判断の相談役をさせたい。連続的（昨日の方針を引き継いで今日見直す）に動かしたい。
- **決定**: 同じ状態（`policy` / `advisor_journal` / 数理計算の事実）を共有する 2 つの AI を持つ。
  - **軸1 夜の分析AI**: cron 起動。方針×状況を突き合わせ見直し提案＋投資日記を生成。
  - **軸2 相談チャットAI**: ダッシュボードのチャットで方針を対話調整し、銘柄・比率を提案。
- **理由**: 「連続的」＝状態の連続性（24 時間プロセスではない）。夜に考えたことを昼に続けられる。方針づくり（対話）と日々の見直し（自動）は入口が違うだけで脳は同じ。
- **代替案**: 単発の分析のみ／チャットのみ → 連続性か対話性のどちらかが欠ける。

## ADR-012: LLM はアダプタで抽象化（OpenRouter 既定・ローカルへ差替可）

- **状況**: LLM プロバイダを後で変えたい。プライバシーが気になればローカル化したい。
- **決定**: LLM 接続を共通インターフェースのアダプタにし、`.env` のキー・URL・モデル名で切り替える。既定は OpenRouter。
- **理由**: OpenRouter 自体が多モデルルーターでモデル選択も柔軟。母艦はラズパイのまま動く。将来 Mac mini ＋ Ollama でローカル完結に**コード変更ゼロ**で移れる。
- **補足（プライバシー）**: クラウド LLM には保有銘柄・方針が送信される。利用者は当面これを許容。気になれば no-logging プロバイダ、最終的にはローカル LLM へ。
- **補足（コスト・モデル品質）**: 既定モデルは **Tool Calling に確実に対応する品質帯**を選ぶ（安すぎるモデルは Tool 精度が落ち「数値を作らない」規律が破れる）。夜間バッチ毎晩＋チャットでフルプロンプト（CORE＋POLICY＋カタログ＋Tool 往復＋日記）を投げるため、想定モデルと概算コストは Phase 3 着手時に見積もる。プロンプトのトークン肥大（カタログ全列挙等）にも注意。
- **補足（弱モデルの割り切り）**: 既定は Tool Calling 確実な強モデル（クラウド）。ローカル弱モデル（例 `qwen3.5:9b`）は **開発時の動作確認用**で、弱モデルが `submit_journal` 等の Tool を確実に呼ばないのは **仕様として割り切る**（弱モデルを動かすための作り込みはしない）。壊れた LLM 出力への防御は [ADR-018](#adr-018-無人運用の障害時方針失敗を黙って放置しない)/[ADR-030](#adr-030-proposed_policy_change-は単一-field-to-に構造強制するfield-は-policy-列の-enum) が担い、これはモデル品質と独立。
- **代替案**: 単一プロバイダ直結 → 移行が固くなる。

## ADR-013: 投資方針 `policy` は単一・チャットで育てる（版管理機構は作らない）

- **状況**: 「テンバガーハンター」等のペルソナ切り替えも検討したが、利用者の本来の欲求は「自分の状況・欲求をチャットで話し、AI と一緒に 1 つの方針を更新していく」こと。
- **決定**:
  - `policy` は**単一のアクティブな方針**（複数ペルソナ切り替え・雛形システムは作らない）。
  - 方針は **ハイブリッド構造**: 構造化コア（リスク許容度・時間軸・現金比率・1 銘柄上限・業種上限・目標リターン・`no_leverage`・除外）＋ **自由文の理念（`rationale`）**。
  - 構造化制約は**そのまま最適化エンジンの制約**にもなる。
  - **版管理の独立機構は作らない**。方針が変わるたび、その日の `advisor_journal` に `policy` のスナップショットを残し、それを履歴とする。
- **理由**: 単一ユーザーに重厚な版管理は過剰。日記スナップショットで「方針がどう進化したか」は十分辿れ、将来差分/ロールバックが欲しくなっても後から作れる。
- **具体例（利用者の言葉の翻訳）**: 「資産が小さいので短期はリスクを取ってリターンを大きく」→ `risk_tolerance:高` / `target_return:高め`。「ゼロカットまで許容」→ **個別銘柄の全損は受容するが信用取引の追証・借金は負わない**＝ `no_leverage:true`。「マイナスにしたくない」→ 現金バッファ・1 銘柄上限で全体の大損を回避。AI は「高リターンと損失回避のトレードオフ」を炙り出して構造化制約に翻訳する。
- **代替案**: 複数ペルソナ切り替え＋厳密な版管理 → 過剰で不採用。

## ADR-014: AI は計算しない（Tool Calling 原則）／RAG は後付け

- **状況**: LLM に「どう思う？」と丸投げすると、一般論や捏造数値しか返らない。
- **決定**:
  - **Python が事実（数字）を計算し、LLM は事実の上で判断する。** LLM には Python が計算済みの構造化された事実だけを **Tool Calling（関数呼び出し）**で渡し、計算はさせない。
  - 投資手法の論文・ニュースを参照させる **RAG は最初は作らない**。論文が数本なら「手法カード（要約）を直接プロンプトに差し込む」で足りる。知識ベースが増えたら `sqlite-vec` で RAG 化（SQLite のまま）。
  - 手法の扱い（実装＝コード／手法DB＝索引／参照知識）は [ADR-016](#adr-016-手法はコードで実装する手法db-は索引でありコードの代替ではない) を参照。参照知識（③）は初期はリポジトリ markdown で管理する。
- **理由**: ハルシネーション防止と、判断根拠の透明性。RAG は有用だが初期は過剰。
- **代替案**: LLM に生データを丸投げ／最初から vectorDB → 捏造リスク・過剰実装で不採用。

## ADR-015: システムプロンプトを「不変 CORE ＋ 可変 POLICY」に分離する／専門性は CORE・Tool・手法カードに宿す

- **状況**: 素の LLM に「この銘柄どう思う？」と聞いても素人の感想しか返らない。**専門家として機能する AI** にしたい。`policy`（投資方針）はユーザーの好みでしかなく、その手前にある「AI を専門家たらしめる層」が未定義だった。また、その専門性の核がチャットで頻繁に変わるのは避けたい。
- **決定**: AI Advisor のシステムプロンプトを 2 層に分ける。
  - **CORE（不変・専門性）**: AI を規律あるクオンツ投資アナリストにする層。①役割、②方法論（分析の作法＝"スキル"）、③規律・ガードレール（数値は Tool の戻り値のみ・不確実性の明示・トレードオフ提示・断定回避）、④Tool の使い方、⑤出力の型（根拠とリスクを必ず添える）。
  - **POLICY（可変）**: `policy` をコンパイルしたユーザー方針。
  - 専門性は「丁寧に質問すること」ではなく、**3 つの不変資産**に宿す: (1) CORE プロンプト、(2) Tool ライブラリ（実計算＝分析スキルのコード化）、(3) 手法カード（論文等のドメイン知識）。
- **置き場所（CORE の安定性を物理的に担保）**:
  - **CORE = リポジトリ内のプロンプトファイル**（例 `backend/advisor/core_prompt.md`）。jj で版管理し、**意図的なコミットでしか変わらない**。チャット AI は触れない。
  - **POLICY = DB の `policy` テーブル**。チャットで気軽に育つ。
  - この物理分離により「コアがコロコロ変わる」が構造的に起こり得なくなる。
- **理由**: tmp2 の「ペルソナをプロンプトで定義」「分析手法をスキルに」の核心を、プリセット切り替えではなく**専門性の CORE 化**として実現する。CORE/POLICY を分けることで、専門家としての規律を保ちつつ、ユーザー方針だけを柔軟に変えられる。
- **詳細**: プロンプト構成・Tool 一覧・手法カード・相談フローは [advisor.md](advisor.md) に記述。
- **代替案**: CORE と POLICY を一体で DB に持ち、チャット AI が両方編集 → コアが drift するため不採用。複数プリセット・ペルソナ切り替え → [ADR-013](#adr-013-投資方針-policy-は単一チャットで育てる版管理機構は作らない) で不採用済み。

## ADR-016: 手法はコードで実装する／手法DB は索引でありコードの代替ではない

- **状況**: 「投資手法（リードラグ PCA、一目均衡表のシグナル、モメンタム等）を、手法カード（プロンプトに差し込むテキスト）として持ち、AI がその場で計算する」案を検討した。当初の advisor 設計は「実装する手法」と「参照する知識」をやや曖昧に"手法カード"とまとめており、手法を非コードで扱えるかのように読めた。
- **決定**: **計算・ロジックを持つ手法は、必ずテスト済みのコードとして実装する。** 手法を 3 層に分ける。
  - **① 実装（コード）**: テスト済み Python。`signals` に事前計算。計算の真実はここだけ。
  - **② 手法カタログ／索引（"手法DB"）**: ①の各手法のメタデータ。AI が「どの手法を使うか」を選ぶための索引。**計算はしない**。コードのレジストリから自動生成し、説明と実コードがズレないようにする。
  - **③ 参照知識（prose・計算なし）**: 論文の所見・相場メモ等。AI が読んで判断材料にする。
  - **②手法DB は①コードへの索引であって、コードの代替ではない。** RAG が効くのは主に②の手法選択と③の参照知識。
  - その場コード生成は**使い捨ての探索的分析に限り**許容し、気に入った手法は①へ昇格させる。本番の信頼できる手法をその場生成で回すのは禁止。
- **理由**: LLM にその場計算させると **(1) 再現性が無く backtest が無意味**（毎回コードが変わる）、**(2) 細かな定義を黙って間違える**（金を扱う判断で致命的）、**(3) 4000 銘柄の事前計算ができない**、**(4) 遅く高コスト**。手法をコード化すれば正確・決定的・高速・検証可能になる。上流コスト（手法ごとのテスト済みコード）は、金を扱うツールの正しいコスト。
- **段階**: ②カタログは初期はコードのレジストリ（全手法をプロンプトに列挙）。手法が大量化したら `method_cards` テーブル＋ embedding（`sqlite-vec`）で意味検索に移す（[data-model.md](data-model.md)）。
- **代替案**: 手法カード＋AI の都度コード生成で統一 → 再現性・正確性・事前計算の要件を満たせず不採用。最初から vectorDB で手法選択 → 手法が少数のうちは過剰、不採用。

## ADR-017: SQLite を定期バックアップする

- **状況**: `policy`・`advisor_journal`・`transactions`・`holdings`・`cash` は手入力の一点もので再取得できない。DB はラズパイの SD カード上に唯一存在し、`data/` は git 管理外。
- **決定**: `assetvane.db` を**定期バックアップ**する（別ディスク／クラウド等）。SQLite は `VACUUM INTO` / `.backup` でオンラインコピー可能。
- **理由**: SD カード故障・WAL 破損で、投資日記・方針・取引・現金が**復元不能**になるのを防ぐ。日米株データは再取得できるが、自分のデータは戻らない。
- **補足**: バックアップ先を外部に置く場合、DB には保有・資産・方針が**平文**で入る点に留意（[ADR-012](#adr-012-llm-はアダプタで抽象化openrouter-既定ローカルへ差替可) のプライバシー懸念が DB 側にも及ぶ）。
- **代替案**: バックアップ無し → 一点ものの全消失リスクのため不採用。

## ADR-018: 無人運用の障害時方針（失敗を黙って放置しない）

- **状況**: 常時無人でデータ取得・指標計算・夜の分析AI が回る。途中で落ちても誰も気づかないと、signals が古いまま・日記が書かれないまま放置される。
- **決定**:
  - **夜間バッチ失敗時は `DISCORD_WEBHOOK_URL` へエラー通知**（気づけるように）。
  - **J-Quants 429/障害**: レート制限を守りリトライ。部分失敗は `fetch_meta` で再開可能（冪等・UPSERT）。
  - **LLM 失敗/タイムアウト（ハード失敗）**: リトライし、ダメなら例外を上位へ伝播してその日の日記をスキップ（signals は前日分が残る）。
  - **無応答＝縮退（observations 空・例外なし）**: 例外を出さず正常終了したのに中身が空（実質何もしなかった晩）も**同じく失敗扱い**でスキップする。これが「静かな失敗」に最も近い穴。journal は「**observations が非空のときだけ書く**」を不変条件とし、空・失敗なら書かない。
  - **通知は runner 集約に一本化**: 夜AI ジョブ（`run_advisor`）自身は `notify.error` を呼ばず、ハード失敗・縮退とも `JobResult.ok=False` で返して **runner の既存集約通知**が 1 通 Discord に出す（batch-pattern と [ADR-036](#adr-036-バッチは停止できる状態が見える実行状態はメモリ-singleton停止は協調キャンセル) の `JobResult` 集約に揃える）。
- **理由**: 無人運用の前提では「静かな失敗」が最大のリスク。最低限の可観測性（エラー通知）と冪等性で回復可能にする。
- **代替案**: エラー処理を後回し → 無人運用と両立しないため不採用。

## ADR-019: 保有は `transactions` から導出する（`holdings` を直接編集しない）

- **状況**: 当初 `holdings` に `shares`・`avg_cost` を直接持つだけで、取引履歴テーブルが無かった。買い増し・一部売却で平均取得単価を正しく更新できず、`asset_snapshots.pnl` の原価も追えない。
- **決定**: `transactions`（買い/売り）を一次データとし、`holdings`（保有株数・平均取得単価）はそこから**導出**する。約定後にユーザーが取引を記録する。
- **理由**: 「提示のみ・手動発注」でも、平均取得単価と損益は取引履歴が無いと正しく出せない。提案の採否・結果の振り返り（[proposals](data-model.md) と併せて）にも履歴が要る。
- **代替案**: `holdings` 直接編集のみ → avg_cost/P&L が破綻するため不採用。

## ADR-020: 個別銘柄ドシエ（定性ファンダ調査）— 1銘柄1レポートを更新し続ける

- **状況**: 数理・ML（数字）だけでなく、ニュース・適時開示・財務を読んだ**定性的なファンダ調査**を個別銘柄でやりたい。全銘柄は無理なので、watchlist（夜・自動）＋ オンデマンド（チャットで「この銘柄調査して」）に限定。
- **決定**:
  - **1 つの調査パイプライン `investigate_stock(code)` を、夜間バッチ（watchlist 巡回）とチャット Tool の両方から呼ぶ**（2 軸 AI と同じ「1 つの脳・2 つの起動口」）。
  - **`stock_dossiers`（1 銘柄 1 行）**: AI 生成の要約を `summary_md`（markdown）で持ち、ずっと更新する living document。`last_investigated_at` を持ち、watchlist 一覧で「最終調査日」を表示して再調査を促す。
  - **`dossier_sources`（ソース台帳）**: 取り込んだ各ソースを **URL（重複防止の UNIQUE）＋短い要約＋発行日＋種別＋銘柄 FK** で記録。**記事全文は保存しない**（取得→要約→本文は捨て、要約と URL だけ残す）。
  - **`source_type` 列で拡張**: `news` / `disclosure` / `twitter` 等。将来 Twitter/X 等を足しても同じ台帳に入る。
  - **取得手段は httpx 一本（NewsAdapter）**: 当初想定した「昼=MCP リッチ／夜=httpx 軽め」の **2 系統は撤回**し、**昼夜とも同じ httpx 取得＋AI 要約**にする。`NewsAdapter` は **Google News RSS 検索 → batchexecute で実 URL 復元 → `trafilatura` で本文抽出 → 既存 LLM `generate_once(source="dossier")` で記事ごと要約 → 本文は破棄して要約と URL のみ残す**（後述「取得→要約→本文破棄」と同じ）。本文抽出が httpx＋`trafilatura` で十分得られると分かったため、ヘッドレスブラウザ（MCP）を夜の無人 cron に持ち込む前提は不要になった。`fetch_news` の `mode`（nightly/chat）引数は**昼夜で取得手段を分けないため廃止**した（dead code 化）。
  - **MCP は将来の選択肢として残す（今回スコープ外）**: Google の URL エンコード仕様変更・`429`/`403`・JavaScript 必須サイトで httpx＋`trafilatura` では本文が取れない場合の**代替取得手段**として将来検討する。今回は実装しない（撤回したのは「昼夜で取得手段を分ける」運用であって、MCP という選択肢そのものではない）。
  - **マルチフェッチャ構成**: 源ごとに前提（URL 復元の要否・RSS 形式・レート制限）が違うため、共通 RSS 抽象でまとめず**源別の fetch 関数**に分ける。今回実装するのは **Google News フェッチャ 1 個**のみ。Yahoo!ファイナンス / ニュース API は**差込口（マージ＋URL 重複排除の口）だけ**用意し、本体は後付け。
  - **取得レベルを `dossier_sources.extraction_status` で記録**（落とさない 3 段フォールバック）: ① 本文取得成功 → AI 要約 → `'summarized'`、② 本文ダメだが og/meta description は取れた → それを要約に採用 → `'description'`、③ どちらもダメ → 見出しのみ・要約なし → `'headline'`。どの段でも見出しは `summarize_dossier` がドシエ合成に使うので無駄にならない。
  - 発行が直近（例 1 週間以内）の新着のみ、URL で重複排除して取り込む。重複排除・保存キーは**復元後の媒体 URL を基本**にしつつ、復元失敗時は Google URL を使う（`dossier_sources.url` の UNIQUE を壊さない）。
- **データ源の段階**: 初期は **財務（J-Quants Free）＋ 一般ニュース（Web/MCP）**。**適時開示（J-Quants TDnet アドオン）は有料**で、課金はしばらく後なので**後付け**。一般ニュースの安定した無料 JP 源は不確実なため、当面は AI の Web 取得で代替。
- **保存場所**: ドシエは**頻繁に AI が自動更新する揮発的・per 銘柄データ**なので **DB**（リポジトリ markdown ではない。CORE/手法カードを repo に置く判断とは逆＝[ADR-015/016](decisions.md) との対比）。中身が markdown なのは「列の型」の話。
- **理由**: 「1 銘柄 1 レポートを更新し続ける」が利用者の自然なイメージ。全文を溜めるのはストレージ・著作権的に不要で、要約＋URL で足りる。台帳に銘柄 FK と URL UNIQUE を持つことで、重複防止と「この銘柄のソース一覧／最終調査日」が成立する。
- **代替案**: ニュース全文をテーブル保持 → 不要・重い、不採用。ドシエを repo markdown → AI 自動更新が jj と相性悪く、鮮度クエリ・紐付けもできず不採用。

## ADR-021: 開発・本番ともコンテナ（Docker Compose）で動かす

- **状況**: 母艦はラズパイ（Linux）。backend（FastAPI/Python）と frontend（Next.js/Node）の 2 言語ランタイムが要る。ホスト直インストールだと「手元では動くがラズパイで動かない」環境差が起きやすい。
- **決定**: 開発・本番とも **Docker Compose** で動かす。backend・frontend を各コンテナにし、**SQLite はファイルなので DB コンテナは作らず** named volume（`data/`）で永続化する。
- **理由**:
  - Linux ネイティブの Docker はカーネル共有で実行時オーバーヘッドが実用上無視でき、**ラズパイでも性能は落ちない**（VM ではない）。
  - **dev/prod parity**。2 言語ランタイムの再現を Compose に固定でき、環境構築の差異を消せる。
  - SQLite は単一ファイルなので DB コンテナ不要。DB に触れる OS プロセスは FastAPI 1 つ（[ADR-002](#adr-002-データベースは-sqlitewal-モード)・[ADR-005](#adr-005-db-に触れるのは-fastapi-のみnext-は-rest-経由)）のまま。
- **運用上の注意**:
  - ラズパイは **SD カードの I/O・寿命**がボトルネック。Docker レイヤ/ログ＋WAL 書き込みで酷使するため **USB SSD ブートを推奨**（[ADR-017](#adr-017-sqlite-を定期バックアップする) のバックアップとも直結）。
  - **ARM の native ビルド**（TA-Lib(C)・LightGBM）は重く失敗しやすい。**イメージは別 PC でクロスビルドし、ラズパイは pull のみ**（学習も別 PC ＝[ADR-006](#adr-006-機械学習の学習は別-pcラズパイは推論のみ) と同じ発想）。
  - Next 本番は **standalone output** でイメージと常駐メモリを抑える（Next 採用で Node 常駐は不可避＝[ADR-022](#adr-022-フロントのビルドは-turbopackvite-不採用adr-004-の補足)）。
  - **メモリ**: 8GB でも FastAPI＋Node(`next start`)＋夜間バッチ（pandas/最適化）の同時稼働は余裕が大きくない。夜間バッチのピークと常駐を意識する。
- **代替案**: ホスト直インストール（venv＋node 直）→ 環境差・再現性で不採用。k8s 等 → 単一ノードに過剰。

## ADR-022: フロントのビルドは Turbopack（Vite 不採用）／ADR-004 の補足

- **状況**: ビルドツールに **Vite** を使いたい希望があった。だが **Next.js は Vite を使えない**（独自パイプライン、webpack→Turbopack）。「Next を使う」と「Vite を使う」は**択一**。一方 [ADR-005](#adr-005-db-に触れるのは-fastapi-のみnext-は-rest-経由) で Next は UI 専用・データは REST 経由と決めており Next の SSR/RSC をほぼ使わないため、構成適合だけ見れば **Vite + React SPA** の方が高かった（本番が静的配信で Node 常駐も不要になる）。
- **決定**: [ADR-004](#adr-004-フロントnextjs--バックfastapiスタック-a)（React/Next を学ぶこと自体が目的）を優先し、**Next.js (App Router) を維持**。ビルドは **Turbopack** を使う（Vite は採用しない）。
- **理由**: 学習目的が Next 固有（App Router/RSC/SSR）に向いており、Vite SPA に替えると目的が損なわれる。Turbopack は Next 標準の高速ビルド系で Vite 的な開発体験に近い。
- **注意**: `next dev --turbopack` は stable。`next build --turbopack` は時期により beta のことがあるため、**Phase 0 で実機確認**し、不安定なら webpack build にフォールバックする。本番は standalone output（[ADR-021](#adr-021-開発本番ともコンテナdocker-composeで動かす)）。
- **トレードオフ（記録）**: Vite と、Biome の Next 固有 lint の一部を諦める（[ADR-023](#adr-023-lintformat-は-biometsucruffpyrightpython)）。本番に **Node 常駐**が要る（SPA なら静的配信で常駐不要だった）点も受容する。
- **代替案**: Vite + React SPA（構成最適・ラズパイ軽量だが Next 学習不可）／ TanStack Start（Vite ベースだがオーバースペック）→ 学習目的優先で不採用。

## ADR-023: Lint/Format は Biome(TS)＋uv/Ruff/pyright(Python)

- **状況**: 2 言語（TS/Python）のツールチェーンを揃えたい。高速・低設定を志向。
- **決定**:
  - **frontend（TS）**: **Biome** を lint＋format に採用（ESLint/Prettier の代替）。
  - **backend（Python）**: **uv**（パッケージ/venv 管理）＋ **Ruff**（lint＋format）＋ **pyright**（型チェック）。
- **理由**: Biome/Ruff/uv は Rust 製で高速・設定が軽く思想が揃う。pyright は型チェックが速く VS Code と相性が良い。
- **注意**: Biome には **Next 固有 lint**（`eslint-config-next` の RSC/Image 等）が無い。これらのチェックは諦め、レビュー/手動で補う。ESLint を併用するとツールが二重化するため、**原則 Biome 単体**とする。
- **代替案**: ESLint+Prettier / pip+venv / mypy → 速度・設定簡素さで不採用（mypy は枯れているが今回は pyright を選択）。

## ADR-024: AI Advisor チャットを全ページ常駐にする（フローティング）

- **状況**: ユーザーは「ダッシュボードの数字や調査結果を**見ながら**相談したい」。専用チャットページに移動したり、ページ遷移のたびに会話が切れると、この製品の肝である「状態の連続性」が UI レベルで損なわれる。
- **決定**: 相談チャットAI（軸2）を、**全ページ共通のフローティング UI として常駐**させる。
  - **Next.js の root layout に置き**、ルート変更（ページ遷移）でアンマウントさせない。これにより会話が遷移をまたいで保持される。
  - フローティングボタンから開閉し、**ドラッグ移動・リサイズ・最小化**できる（実装は `react-rnd` 等の標準手段で足りる）。
  - 会話状態と窓の位置/サイズはクライアントで保持（`localStorage` 等）。**会話履歴の永続実体（保存先）は実装時に決める**。
  - Tool の実行を UI に明示する（「AI は計算せず Tool の事実で答える」＝[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け) を可視化）。
- **理由**: 「状態の連続性」（[ADR-011](#adr-011-ai-advisor-を-2-軸夜の分析ai相談チャットai-で実装する製品の核心)）を画面体験として実現する。root layout 配置はフレームワーク標準の素直な方法で、特別な仕掛けが要らない。
- **代替案**: ページごとに別チャット／専用チャットページのみ → 「見ながら」と連続性が損なわれるため不採用。
- **詳細**: 画面構成・常駐チャットの挙動は [screens.md](screens.md) を参照。

## ADR-025: 画面コンテキスト注入は軽量ヒントのみ（数値は渡さない）

- **状況**: 常駐チャット（[ADR-024](#adr-024-ai-advisor-チャットを全ページ常駐にするフローティング)）で「**これ**調査して」「**この**集中度どう？」のような**指示語**を解決したい。素朴には「今見えている画面の数値」をプロンプトに載せたくなるが、それは (1) チャットが進むほど**トークンが無駄に肥大**し、(2) **生データを丸投げ**して LLM に数値を扱わせることになり [ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け) の規律を破る。
- **決定**: チャット（軸2）のリクエストに、**「ユーザーが見ているページ＋主対象」だけ**を軽量に渡す。
  - 構造は `page` ＋ 任意の `focus`（例 `{ type: "stock", code: "6920" }`）。対象が無いページは `focus` 省略。
  - プロンプトには**1 行の自然文にコンパイル**して差す（例: 「銘柄 6920 の詳細ページを見ている」）。
  - **数値・画面データそのものは載せない**。AI は必要に応じて該当 Tool（`get_signals(6920)` 等）を呼んで**事実を取り直す**（＝「必要に応じて参照できる状態」）。
  - 画面コンテキストは**揮発情報で DB に保存しない**。軸1（夜の分析AI・cron）は画面が無いので**コンテキスト無し**。
- **理由**: 指示語の解決には「何を見ているか」が分かれば足り、数値は Tool で取れる。トークンを節約しつつ [ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け) の「AI は計算しない／生データを丸投げしない」を守れる。
- **代替案**: 画面の全数値を渡す → 肥大・規律破りで不採用。コンテキスト無し → 指示語が解けず体験が落ちるため不採用。
- **詳細**: プロンプトへの注入は [advisor.md §6.1](advisor.md)、画面側の扱いは [screens.md §5](screens.md) を参照。

## ADR-026: signals は連続スコアの「材料」。AI が主消費者で、閾値は破壊的ゲートにしない

- **状況**: 当初の Phase 1 設計は「検知条件（ゴールデンクロス成立・出来高 3 倍超）を満たした行だけ `signals` に保存し、一覧にスコア順で出す」だった。だが利用者の本来の使い方は「**シグナル一覧を人が見て判断する**」のではなく「**AI Advisor が複数の手法で銘柄を確認し、根拠つきで提案する**」（[ADR-011](#adr-011-ai-advisor-を-2-軸夜の分析ai相談チャットai-で実装する製品の核心)・[advisor.md](advisor.md)）。この前提だと、閾値で「クロス目前(near-miss)だが他のシグナルは強い」銘柄を**保存時にバッサリ捨てる**と、AI の判断材料が消える。
- **決定**:
  - `signals` は **AI に渡す「材料」**であり、`signal_type` ごとの `score` は **0..1 の連続値**にする（イベントの 0/1 ではない）。momentum も「今日クロスしたか」ではなく**連続の上昇トレンド強度**として算出し、クロス・反転は加点ブースターにする。
  - **閾値（例 volume_spike の 3.0 倍・momentum のクロス）は「保存時の破壊的ゲート」にしない**。`notable` フラグ＋既定の表示カットオフに格下げし、**夜間バッチは低フロア以上の行を広めに保存**して near-miss を残す。絞り込みは**読み取り時**に行う —— AI は `screen_stocks(min_score=…)` で自分でカットオフを動かし、個別銘柄は `get_indicators(code)`（都度計算・[ADR-016](#adr-016-手法はコードで実装する手法db-は索引でありコードの代替ではない) の手法コード）で**フィルタ無しの全数値**を見られる。
  - **human 向けの `/signals` 一覧は副産物**。主経路は「AI が Tool で材料を読み、根拠とリスクを添えて提案する」（[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け)）。
- **理由**: AI が判断するなら、AI には**連続的な濃淡**を見せるべき。読みやすさのための事前フィルタは人間向け UI の都合であって、データ生成の段階で情報を捨てる理由にはならない。連続スコア＋低フロア保存なら、人間一覧（高スコア順）も AI の柔軟な絞り込みも両立できる。
- **代替案**: 検知条件で硬く絞って保存（near-miss を捨てる）→ AI の材料が痩せるため不採用。全 4000 銘柄を毎日フル保存 → 大半がノイズでストレージ過大、低フロアで足りるため不採用。
- **詳細**: Phase 1 の式・既定値・保存フロアは [phase-specs/phase1-spec.md](phase-specs/phase1-spec.md) §4、保存量の足切りは `MOMENTUM_FLOOR`/`VOLUME_FLOOR`。

## ADR-027: 手法パラメータは Phase 1 はコード定数、将来 method_settings（AI は助言・自動改変しない）

- **状況**: momentum/volume_spike の閾値・重み（例 `W_TREND`・`VOLUME_FLOOR`）のような**手法のパラメータ（ツマミ）**を、どこに置き・誰が・どう調整するかが未定だった。利用者は「magic number のお守りはしたくない・理想は AI に任せたい・でもまず動かしてすぐツマミで直したい」と整理した。「`signals` テーブルに汎用列 A/B/C/D を持たせて UI から回す」案も検討された。
- **決定**:
  - **置き場所**: パラメータは `signals`（結果テーブル）には持たせない（結果と設定の混同・意味の消失）。手法は **①コードが名前付き定数で既定値を持つ**（`momentum.py` の `W_TREND=0.6` 等）。
  - **Phase 1**: パラメータは**コードの名前付きモジュール定数のみ**。**env も使わない**（どうせすぐツマミ化するので中間層を作らない）。直書きの magic number は禁止し、必ず名前付き定数で外出しして将来移行を楽にする。
  - **将来（Phase 3 以降）**: **`method_settings`**（手法 × 名前付きパラメータ → 値）テーブル＋**WebUI 編集**＋「チャットで AI に相談 → 提案 → 承認で反映」を足す（[policy](#adr-013-投資方針-policy-は単一チャットで育てる版管理機構は作らない) と同じ「育てる」型）。手法カタログ（[ADR-016](#adr-016-手法はコードで実装する手法db-は索引でありコードの代替ではない) ②）が「どの手法にどのツマミがあるか」を宣言する。
  - **AI の関与**: AI は**パラメータを助言**してよい（[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け) の「解釈・提案」）が、**毎晩こっそり自動改変はしない**。パラメータが勝手に変わると backtest が無意味になる（再現性＝[ADR-016](#adr-016-手法はコードで実装する手法db-は索引でありコードの代替ではない)）。変更は policy と同じく**意図的な操作**（UI 編集 or 承認）に限る。
  - **「機械に重みを学ばせたい」路線**は、ルール手法のツマミ自動改変ではなく **ML 手法（`ai_alpha`・[Phase 5](roadmap.md)）**が担当する。手で決める手法と機械が学ぶ手法を**別の軸**として分ける。
- **理由**: ツマミを UI から回したい欲求は妥当だが、結果テーブルへの汎用列は構造を壊す。再現性のため自動改変は避けつつ、AI 相談 → 意図的反映で「任せたい」も満たす。手法 2 個の初期に設定基盤を先行投資しない（[ADR-016](#adr-016-手法はコードで実装する手法db-は索引でありコードの代替ではない) 「増えてから仕組み化」）。
- **代替案**: `signals` に汎用パラメータ列 → 結果と設定の混同で不採用。env で全部可変化 → すぐ method_settings に移すため中間層は作らない。AI が自動チューニング → 再現性破壊で不採用（学習は ML 手法で別軸）。
- **詳細**: Phase 1 の定数は [phase-specs/phase1-spec.md](phase-specs/phase1-spec.md) §4、段階化は [phase-specs/_open-questions.md](phase-specs/_open-questions.md) U-1/U-2。

## ADR-028: LLM コストガードレール（監視と上限・3 値トグル・env 既定＋設定 UI 上書き）

- **状況**: クラウド LLM（OpenRouter・ADR-012）利用中は、夜間バッチ（毎晩 1 回）＋昼チャット（軸2）でトークン課金が積み上がる。月額の許容線をどう扱うか未定だった（[_open-questions.md](phase-specs/_open-questions.md) U-5）。grill での試算では**夜間バッチは安く**（$10/月オーダー・無人 1 回／晩）、**コストの主犯は昼チャット**（Phase 3 は履歴フル送信なので会話が伸びるほど 1 往復が重くなる）。なお**最終的にはローカル LLM（Ollama）移行でコストは消える**前提。
- **決定**:
  - **月額しきい値の既定 = $50**。挙動は **3 値トグル**: `off`（監視しない）/ `warn`（**既定**・LLM 呼び出しは止めず Discord 通知＝ADR-007 ＋画面バナーで知らせる）/ `block`（しきい値超過で LLM 呼び出しを止める）。
  - **既定は `warn`**。無人の夜間バッチが予算切れで**黙ってスキップしてその晩の journal を欠く**のを避けるため、通知が本体・ブロックは任意機能に格下げ。
  - **支出の計上**は OpenRouter がレスポンスに返す**実コスト（`usage.cost`）を読んで**小さな `llm_usage` 台帳（or 月次カウンタ）に積む。**単価表を自前で持たない**。Ollama は cost フィールドが無いので $0 扱い → トグル OFF と合わせて自然に無害化。
  - **設定の層**: しきい値・トグルは **env を起動既定**にしつつ **DB ＋設定 UI で上書き編集**する 2 層。これは運用設定なので env 既定アリ（手法パラメータの [ADR-027](#adr-027-手法パラメータは-phase-1-はコード定数将来-method_settingsai-は助言自動改変しない) が「env を挟まない」としたのとは**別ルール**）。同じ設定 UI に夜間ドシエの `N`（U-8）等の運用ツマミも同居させる。
  - **位置づけ**: これは**クラウド LLM 期間限定のガードレール**。ローカル LLM 移行後はトグル OFF。作り込みすぎない。
- **理由**: 個人ツールでアドバイザーが月の途中に突然死ぬのは体験が悪い。「気づける（通知）」が本体で、ハードブロックは保険。OpenRouter の実コストを使えば見積り誤差なく安く実装できる。
- **代替案**: ハード上限のみ → 無人夜間バッチが黙ってスキップで不採用（任意モードに残す）。単価表を自前管理 → OpenRouter 実コストで足りるため不採用。env のみ（UI 無し）→ 画面で線引き・トグルしたい要望に反するため不採用。
- **詳細**: Phase 3 spec のコストガードレール節、[_open-questions.md](phase-specs/_open-questions.md) U-5。

## ADR-029: 昼チャットの会話は揮発（localStorage）＋重要点は承認付きで journal に昇格

- **状況**: 常駐チャット（軸2・[ADR-024](decisions.md)）の会話履歴をどこに永続するか（[_open-questions.md](phase-specs/_open-questions.md) U-6）。会話の DB 永続（検索可）は単一ユーザー（[ADR-001](decisions.md)）には過剰。一方で「重要な相談・取った投資行動」はどこかに残したい、という要望があった。
- **決定**:
  - **生の会話スクロールバックは frontend の `localStorage`**。サーバはステートレス維持（毎ターン全 messages 送信）。※当初文言「揮発・リロードで消えてよい」は**誤りなので修正**: localStorage は**同一ブラウザで永続**し、消えるのは「ブラウザのデータ消去」「別デバイス/別ブラウザ」のときだけ（リロード・再起動では消えない。揮発するのは `sessionStorage`）。個人・単一デバイスにはこの性質が好都合。
  - **残すべき重要点は明示的に `advisor_journal` へ昇格**する。昼チャットに「この会話を要約して journal に残す」**トリガー（手動アクション＋AI の自発提案）**を置き、**書き込みは必ずユーザー承認後**（黙って自動保存しない＝[ADR-014](decisions.md)）。
  - journal の**夜エントリと昼要約エントリは `advisor_journal.source`（`'nightly'` | `'chat'`）で区別**し、同一テーブル・同一 Journal 画面を再利用（別テーブルを作らない）。
  - **投資行動の数字は `transactions` が正**（holdings 導出元＝[ADR-019](decisions.md)）。journal 要約はそれを**物語として参照するだけ**で、数字の真実を持たない。
  - これで「生ログは消えてよい・重要点は故意に昇格」の**二層**になり、昼の気づきが失われない（[ADR-013](decisions.md) の policy snapshot・proposals と並ぶ「残すべきものは別経路で永続」の規律）。
- **理由**: 単一ユーザーに DB 会話永続は過剰だが、重要な決定の取りこぼしは避けたい。明示昇格で両立する。承認制は [ADR-013](decisions.md)/[ADR-027](decisions.md) の規律と揃う。数字を `transactions` に集約するのは [ADR-019](decisions.md) を壊さないため。
- **代替案**: 全会話 DB 永続（検索可）→ 過剰・書き手増で不採用。`sessionStorage`（リロードで消える）→ リロード耐性が無く不便で不採用。AI 自動 journal 書き込み → 規律破りで不採用（承認制）。
- **詳細**: Phase 3 spec §6.4/§6.5/§9.1、[data-model.md](data-model.md) `advisor_journal.source`、[_open-questions.md](phase-specs/_open-questions.md) U-6/U-7。

## ADR-030: `proposed_policy_change` は単一 `{field, to}` に構造強制する（field は policy 列の enum）

- **状況**: Phase 3 実機検証（[_open-questions.md](phase-specs/_open-questions.md) U-10）で、弱い LLM（ローカル 9B）が方針変更案 `proposed_policy_change` に**多フィールド patch**（`{max_position_weight, sector_diversification_limit, target_cash_ratio}` のような複数列同時変更）を渡した。当時の `submit_journal` の JSON Schema は `proposed_policy_change` を**構造ゼロの自由 object**（`additionalProperties: true`）として LLM に見せており、`{field, to}` 形を出力契約のどこでも表明していなかった。一方で適用側 `apply_policy_change`（[ADR-013](decisions.md)）は単一 `{field, to}` しか食えないため、**承認時に `ValueError` で落ちる「適用不能 proposal」が queue に入る**経路があった。さらに `upsert_policy` は列ホワイトリストを持たず、未知 `field` 文字列は適用時に SQL レベルで不可解に落ちる潜在バグもあった。
- **決定**:
  - **契約は [ADR-013](decisions.md) の単一 `{field, to}` を維持**し、出力契約（Function Calling の JSON Schema）を**構造で締めて**多フィールド patch を LLM に出させない。`proposed_policy_change` を `ProposedPolicyChange`（`field`/`to`/任意 `from`/`reason`）のネスト型にし、`required: [field, to]` を立てる。
  - **`field` は policy の構造化コア列の enum**（`risk_tolerance`/`time_horizon`/`target_cash_ratio`/`max_position_weight`/`sector_caps`/`target_return`/`no_leverage`/`exclusions`）に締める。enum の正本は `services/policy.py` の `DEFAULT_POLICY` のキーで、**一致はドリフトガードテストで CI 担保**（新規ハードコード一覧を増やさない）。`rationale` は即時更新（[U-7](phase-specs/_open-questions.md)）で提案対象外なので enum に含めない。
  - **schema を締めても [ADR-018](decisions.md) の防御層は外さない**。弱モデルは schema を破りうる（Function Calling 遵守は非保証）ため、受理側に共有の正規化 `coerce_policy_change` を置き、**単一形に適合しない変更案（多列 patch・非 dict・`to` 欠落・enum 外 field）は None に倒す**。`submit_journal`（受理ゲート）と nightly（proposal 起票判定）の両方が同関数で正規化し、**適用不能 proposal を queue に入れない**。無人 nightly は観測（observations）を巻き添えにせず journal を残す。
  - **`apply_policy_change` に未知 field の防御**（`DEFAULT_POLICY` に無ければ `ValueError`）を足し、承認適用側も SQL 前に弾く（defense-in-depth）。
- **理由**: 真因は「出してほしい形を出力契約に表明していない」ことで、散文プロンプトより**構造制約（schema の enum＋required）**が桁違いに強い。schema = 発生率を下げる予防、`coerce_policy_change` = 破られた時の graceful degradation の**二層**で、弱モデルでも適用不能 proposal が生じない。enum を `DEFAULT_POLICY` に一致させることで LLM 出力側と適用側の両方が安全になる。
- **代替案**: (B) 多フィールド patch を正式対応（`apply_policy_change`・`proposals.body` を複数列 patch に拡張）→ [ADR-013](decisions.md)「1 変更ずつ育てる」に反し契約拡張になるため不採用（多項目を直したい晩は提案を複数起票させる）。散文プロンプトだけで形を指示 → 構造制約より弱く弱モデルで破られるため不採用（補強としては registry の description に 1 文だけ添える）。
- **詳細**: Phase 3 spec §4.4（`submit_journal` 引数）、[_open-questions.md](phase-specs/_open-questions.md) U-10、`backend/app/advisor/tools/schemas.py`（`ProposedPolicyChange`・`coerce_policy_change`）。

## ADR-031: 株式スクリーナー（夜間 valuation_snapshots ＋読み取り時ランク・市場ごとに分離）

- **状況**: `/stocks` を「PER/PBR/時価総額/配当利回りで全銘柄を絞り込み、条件を保存できるスクリーナー」にしたい要望。これらバリュエーション指標は当時どこにも持たず、`financials`（eps/bps）は [ADR-008](#adr-008-j-quants-は-v2x-api-key-を使うv1-は使わない) の Phase 2 で土台だけ組まれたが**未検証・保有銘柄のみ取得**だった。時価総額・配当の取得可否、計算の置き場所、米株の扱いが未定だった。
- **決定**:
  - **データ源は J-Quants 単独**（別ソース不要）。実機検証（2026-06）で **`/v2/fins/summary`**（`/v2/fins/statements` は 403）が `EPS`/`BPS`/`FDivAnn`(予想年間配当)/`DivAnn`(実績)/`ShOutFY`(発行済株式数)/`TrShFY`(自己株式) を返すことを確認。→ **PER=close/EPS・PBR=close/BPS・時価総額=close×(ShOutFY−TrShFY)・配当利回り=FDivAnn/close**。`jquants.md §6`「実フィールド未確定」はこれで解消。
  - **採用行の規律**: BPS は通期(FY)行にのみ入り四半期は空・四半期 EPS は累計のため、**PER/PBR は最新 FY 行の実績 EPS/BPS**、**配当/株数は最新開示行**を採用する（`services/valuation.py`）。配当は予想（`FDivAnn`）優先＝予想配当利回りを既定。
  - **計算・保管は夜間スナップショット**。夜間ジョブ `calc_valuation` が重い結合（daily_quotes × financials）を**1 銘柄 1 行**の `valuation_snapshots` に畳む。`/stocks/screen` は読み取り時にこれを絞り込み、**業種内パーセンタイル・時価総額順位は ~4000 行への window 関数で都度算出**する（[ADR-026](#adr-026-シグナルは高フロア保存読み取り時カット) の「読み取り時に絞る・near-miss を捨てない」と整合。事前フィルタはしない）。値の鮮度は daily_quotes も夜間更新のため「前夜終値ベース」で読み取り時計算と同じ。
  - **数値は Python が計算**（[ADR-014](#adr-014-ai-に数値を計算させない)/[ADR-016](#adr-016-手法はテスト済みコードで実装する)）。`quant/valuation.py` の純関数（赤字 eps<=0・欠損は None で捏造しない）→ `services` が採用行を整えて呼ぶ → `calc_valuation` が焼く。
  - **全銘柄化**: `fetch_financials` を保有銘柄限定から**営業日ループの by-date 一括取得**（fetch_quotes と同型・初回は `full_backfill`）へ拡張。未マスタ銘柄の行は既知 stock コードに絞って FK 違反を防ぐ。
  - **保存フィルタ**は `screening_filters`（`criteria_json` の緩い JSON・単一ユーザーなので `user_id` 無し＝[ADR-001](#adr-001-単一ユーザー認証なし)）。CRUD は `/screening-filters`。
  - **市場ごとに分離**（記録対象）: スクリーナーは通貨・業種分類・財務ソースが市場で異なるため跨がない。**v1 は日本株専用**（J-Quants）。**米株は Phase 7 で `/us-stocks` 別ルート・別スナップショット**（通貨列・FX・GICS が入るタイミング＝[roadmap.md Phase 7](roadmap.md)・[data-model.md](data-model.md) の通貨 YAGNI 節）。`/stocks` のリネームは Phase 7 まで先送り。
- **理由**: バリュエーションは全部「株価」を含み毎日動くが、財務は四半期更新。生データを貯め比率は使う時に掛けるのが素直。ただし**横断ランク（業種内・上位N）**は分布計算が要り、夜間に 4000 行へ畳む土台があると window 関数で一瞬になる（[signals](#adr-026-シグナルは高フロア保存読み取り時カット) と同じ「夜間に焼いて読み取り時に絞る」パターン）。市場分離は「¥と$混在の時価総額」「33業種と GICS 跨ぎの相対ランク」が無意味になるのを避けるため。
- **代替案**: (A) 取得時に PER を焼く → 株価が動くと古くなり毎晩全銘柄再計算が要る（スナップショットと同等の手間で柔軟性が低い）ため不採用。(B) 読み取り時にフル結合で都度計算 → 絶対しきい値だけなら可だが横断ランクのサブクエリが重く書きにくいため、スナップショットに畳む方を採用。(C) 時価総額・配当を外部ソース（EDINET 等）→ J-Quants summary に揃っており不要。(D) 日米一体スクリーナー → 通貨・分類・財務ソースの境界で破綻するため不採用（Phase 7 で別ルート）。
- **TODO（落とさない）**: **テクニカル/シグナル複合フィルタ**（momentum スコア・volume_spike・5日騰落率を screen に追加＝必須機能）、条件結合の **AND/OR・グループ化**（v1 は AND のみ）、**米株スクリーナー `/us-stocks`**（Phase 7）。
- **詳細**: `backend/app/quant/valuation.py`・`services/valuation.py`・`batch/jobs/calc_valuation.py`・`db/schema.py`（`valuation_snapshots`/`screening_filters`）・`routers/stocks.py`（`/stocks/screen`）・`routers/screening_filters.py`・`alembic/versions/0007_screening.py`、[jquants.md](jquants.md) §6（実フィールド確定）。

## ADR-032: codex 接続は MCP＋`codex app-server`（API/codex を面別切替・自動フォールバックなし）

- **状況**: AI Advisor は OpenAI 互換 API（OpenRouter・[ADR-012](#adr-012-llm-はアダプタで抽象化openrouter-既定ローカルへ差替可)）固定だった。**コスト削減**のため codex CLI でも動かしたい（codex は ChatGPT サブスク認証＝API キー不要・限界費用ゼロ。このマシンは `~/.codex/auth.json` で login 済み）。ただし「今まで通り API でも動く」を必ず残す。codex は外部定義の function tool を注入する口を持たず、自前 Tool を渡す正規ルートは MCP のみ。
- **決定**:
  - **自前 Tool は MCP サーバ化**。FastAPI プロセス内に streamable HTTP の `/mcp` を立て、既存 `REGISTRY` を `openai_tools(phase)` と同集合で公開（`app/advisor/mcp_server.py`）。handler は FastAPI 内で動き **DB に触れるのは FastAPI だけ（[ADR-005](#adr-005-db-に触れるのは-fastapi-だけ)）** を保つ。codex は別プロセスで HTTP 越しに呼ぶだけ。
  - **面別 provider 切替**。`chat`/`nightly`/`dossier` を個別に `"openai"`（既定）/`"codex"` 選択（`settings.provider_for`・`app/advisor/engine.py` のディスパッチャ）。既定は全面 openai＝何も設定しなければ従来通り。openai 経路（`llm.py`/`service.py`）は無改修。
  - **codex 駆動は `codex app-server`（stdio JSON-RPC）**。当初 `codex exec` を計画したが、**exec は非対話で MCP ツール呼び出しが常にキャンセルされる既知リグレッション（openai/codex #16685・#24135）**で詰んだ（`--dangerously-bypass-approvals-and-sandbox` 無しでは MCP を呼べない）。app-server は `mcpServer/elicitation/request` をプログラムで accept でき、**危険フラグ不要・read-only サンドボックス維持**で MCP が通る（実機検証済み・codex-cli 0.136.0）。
  - **app-server の抱え方**（`app/advisor/codex_engine.py`）: プロセス内で **1 本だけ常駐シングルトン**として遅延 spawn（死んだら次回再起動）。ターンは **async ロックで直列化**（1 本の stdio に全イベントが混ざるため）。**1 turn = 新規 thread**（stateless・openai 経路と同じく毎回 CORE/POLICY＋全履歴を載せる）。**CORE は thread/start の `baseInstructions`（[ADR-015](#adr-015-システムプロンプトは不変-core可変-policy-に分離) の不変 base ペルソナ枠）・POLICY ほか system は `developerInstructions`**。**tool_runs は `item/completed`（mcpToolCall・server=assetvane）から再構成**（[ADR-025](#adr-025-画面コンテキスト注入は軽量ヒントのみ数値は渡さない)＝結果値は載せない）。usage は cost_usd=0 で計上（[ADR-028](#adr-028-llm-コストガードレール監視と上限3-値トグルenv-既定設定-ui-上書き)）。
  - **障害時は API へ自動フォールバックしない**（コスト削減の意図を裏切らない）。一過性（codexErrorInfo の serverOverloaded 等）は指数バックオフ再試行、恒久・タイムアウト・空応答は `CodexEngineError` → [ADR-018](#adr-018-無人バッチの失敗は握りつぶさず-discord-へ通知) 処理（chat=502／nightly=journal スキップ＋通知）。
- **理由**: MCP 化で「AI は計算しない（[ADR-014](#adr-014-ai-に数値を計算させない)）」「DB は FastAPI だけ（ADR-005）」を破らず codex に自前 Tool を渡せる。面別切替なら chat だけ codex に寄せて nightly は安全に openai 維持できる。app-server は exec の MCP 詰みを回避しつつサンドボックス・無危険フラグを保てる唯一の道。turn 毎 thread は openai の stateless 構造と完全一致しドリフトが無い。
- **代替案**: (A) `codex exec` → MCP キャンセルのリグレッションで不採用（本 ADR の主因）。(B) function tool を直接注入 → codex に口が無い。(C) stdio プロキシ MCP → DB を別プロセスに晒し ADR-005 違反、不要。(D) 障害時に openai へ自動フォールバック → コスト削減の意図に反し、どちらで答えたか不透明になるため不採用。
- **段階化**: **今回は chat=codex を実機検証して緑**。nightly/dossier の codex 化は配線のみ（既定 openai・未実証扱い）。無人 cron での ChatGPT トークン継続（8 日ルール・`auth.json` 上書き禁止）を実証してから寄せる。
- **詳細**: `app/advisor/codex_engine.py`（app-server JSON-RPC シングルトン）・`engine.py`（ディスパッチャ）・`mcp_server.py`（REGISTRY を MCP 公開）・`app/main.py`（`/mcp` マウント）・`app/config.py`（provider＋codex 設定）。protocol は `codex app-server generate-json-schema` で確定（thread/start の `baseInstructions`/`developerInstructions`/`config.mcp_servers`、turn の `item/completed`・`turn/completed`）。

## ADR-033: 銘柄別の調査 cadence（夜間ドシエ巡回を `interval_days`＋夜あたり天井に作り替える）

- **状況**: 夜間ドシエ巡回ジョブ（`investigate_dossier`・[ADR-020](#adr-020-個別銘柄ドシエ定性ファンダ調査-1銘柄1レポートを更新し続ける)）は、当初「**全 watchlist 銘柄を一律 `stale=21 日`固定で stale 判定し、古い順に先頭 `N=3` 固定だけ調べる**」だった。だが利用者から「**この銘柄は毎日調べたい・別の銘柄は月 1 でいい**」という**銘柄単位で頻度を変えたい**要求が出た。一律 21 日・先頭 3 固定ではこれを表現できず、また「毎日見たい銘柄」が他の stale 銘柄に押し出されて回ってこない問題もあった。
- **決定**:
  - **調査間隔を銘柄ごとに持つ**。`watchlist` に **`interval_days`（既定 21＝現状の stale を維持）** を足し、利用者が銘柄単位で頻度を変えられるようにする（プリセット「毎日=1／週=7／月=30」＋任意整数）。
  - **stale 判定を per-row 基準に変える**。「最終調査（`last_investigated_at`）が**その銘柄の `interval_days`** より古い（または未調査）」を巡回対象にする。固定 21 日の横並び判定はやめる。
  - **夜あたりの上限は天井（暴走防止）として残す**。`N=3` の固定枠は廃止し、config の **`DOSSIER_NIGHTLY_MAX`**（env 既定＋[ADR-028](#adr-028-llm-コストガードレール監視と上限3-値トグルenv-既定設定-ui-上書き) と同じ運用設定 UI のツマミ）で `[:cap]` する。`interval_days=1` の銘柄が増えてもコスト（LLM・取得）が暴走しないための保険であって、「毎晩 N 件だけ」という意味づけからは外す（対象が天井を超える晩は古い順に積み残し、翌晩に回る）。
- **理由**: 「1 銘柄 1 レポートを更新し続ける」（[ADR-020](#adr-020-個別銘柄ドシエ定性ファンダ調査-1銘柄1レポートを更新し続ける)）の **更新頻度は銘柄ごとに違って当然**で、利用者の関心の濃淡をそのまま cadence に落とせるべき。間隔を銘柄に持たせ、夜あたりは「件数の割当」ではなく「暴走の天井」として扱うのが素直。`DOSSIER_NIGHTLY_MAX` を env＋UI に置くのは運用ツマミの [ADR-028](#adr-028-llm-コストガードレール監視と上限3-値トグルenv-既定設定-ui-上書き) と揃える。
- **代替案**: 一律 stale=21／先頭 N=3 固定を維持 → 銘柄ごとの頻度差を表現できず利用者要求を満たせないため不採用。間隔を持たせず「毎日全 watchlist を調べる」→ watchlist が増えるとコスト暴走、関心の薄い銘柄まで毎晩叩くため不採用（だから per-row 間隔＋天井）。
- **詳細**: `db/schema.py`（`watchlist.interval_days`）・`db/repo.py`（`add_watchlist`/`list_watchlist`/`set_watchlist_interval`）・`batch/jobs/investigate_dossier.py`（`_select_targets` の per-stock 間隔判定＋`DOSSIER_NIGHTLY_MAX` キャップ）・`routers/watchlist.py`（`interval_days` 出力・更新 endpoint・`stale` を per-row 基準に）・`config.py`（`DOSSIER_NIGHTLY_MAX`）。

## ADR-034: 一般ニュースダイジェスト（銘柄に紐づかないニュースを別系統で持つ・実装済み）

- **状況**: ドシエのニュース取得（[ADR-020](#adr-020-個別銘柄ドシエ定性ファンダ調査-1銘柄1レポートを更新し続ける)）は**個別銘柄に紐づく**ニュースが対象で、台帳 `dossier_sources` は **銘柄 FK（`code`）必須**を前提に設計されている。だが利用者は別途、**銘柄に紐づかない一般ニュース**（その日のホットニュース数件＋世界情勢／マクロ等のカテゴリ）も眺めたい、という要求を持つ。これを `dossier_sources` に無理に載せると「code が無いニュース」が混ざり、銘柄 FK・URL UNIQUE・「この銘柄のソース一覧」という台帳の住所が崩れる。**利用者から「必ず ADR に残して」と明示された**項目で、当初は構想のみ記録していたが、grill-me（`3-adr-034-floofy-hoare`）で設計を確定し実装した。
- **決定（実装済み・確定事項）**:
  - **別テーブル `general_news`**（`0011_general_news`）に持つ。`dossier_sources`（code FK 必須）は個別銘柄ドシエ専用のまま据え置き、`general_news` は **`code` FK を持たず `category` 列を持つ**。`url` UNIQUE ＋ `on_conflict_do_nothing` で再取得の二重取り込みを防ぐ（本文は保存せず summary と url のみ＝ADR-020 の流儀）。
  - **取得**: `NewsAdapter` に新メソッド `fetch_general_news()` を追加し、既存の内部パイプライン（`_fetch_rss_items` / `_process_item` の 3 段フォールバック要約）を再利用する（`fetch_news`〔銘柄専用〕は不変更）。Google News キーワード検索 RSS。**カテゴリ定義（ラベル＋検索クエリ）・件数上限・lookback は定数モジュール `app/adapters/general_news_config.py` に置く**（env / config.py には足さない＝構造データは安定資産でありコードと共に育てる。ADR-010 が禁じるのは接続情報のハードコードであって検索キーワードは別物）。
  - **タイミング**: 夜間バッチに新ジョブ `fetch_general_news.run` を追加し、`NIGHTLY_JOBS` の `run_advisor.run` の**直前**に置く（軸1 が当日の市況文脈を briefing 材料にできるよう先に台帳へ入れる）。冪等・無人 cron 前提（httpx 一本＝ADR-020 改訂）。
  - **消費先（両方）**: ① **Dashboard ウィジェット**（`GET /general-news` → `GeneralNewsWidget` がカテゴリ別に表示）。② **軸1（夜の分析 AI・[ADR-011](#adr-011-ai-advisor-を-2-軸夜の分析ai相談チャットai-で実装する製品の核心)）の briefing**＝新 Tool `get_general_news`（`min_phase=4`）を足し、`_NIGHTLY_INSTRUCTION` で取得を促す。Tool なので**軸2 チャットでも再利用**できる。Discord 通知には含めない（Phase 6 の領域）。
  - **副件（上げ忘れ修正）**: 本実装で `CURRENT_PHASE` を 3→4 に上げた。Phase 4 完了時に上げ忘れており、`min_phase=4` の既存 Tool（`get_dossier` / `investigate_stock` / `fetch_news`）がチャット・夜AI に露出していなかった（夜間 `investigate_dossier` ジョブは handler 直呼びで動いていたため気づきにくかった）。これにより Phase 4 Tool 群と新 `get_general_news` が両軸に露出する。
- **理由**: 個別銘柄ドシエ（[ADR-020](#adr-020-個別銘柄ドシエ定性ファンダ調査-1銘柄1レポートを更新し続ける)）と一般ニュースは**住所（紐づく対象）が根本的に違う**ため、同じ台帳に混ぜず別系統で持つのが正しい。
- **代替案**: `dossier_sources` を code FK 任意に緩めて一般ニュースも載せる → 台帳の住所が崩れるため不採用。`fetch_news` を汎用化して共用 → 銘柄専用シグネチャがぶれるため不採用（新メソッド追加に留めた）。日次総括 markdown を別テーブルに焼く 2 テーブル構成 → 消費先が「眺める＋文脈材料」だけなので YAGNI（必要になれば後付け）。カテゴリ定義を env 化 → 構造データの JSON 文字列化・`.env.example` 同期が煩雑なだけで益が無いため定数モジュールに。
- **段階**: **実装済み（2026-06-06）**。pytest green（adapter / repo / API / job の単体＋migration 回帰）。frontend は Dashboard widget まで配線（専用ページは作らない）。

## ADR-035: ラズパイへのデプロイは「Mac(arm64) ローカルビルド → ghcr.io → ssh デプロイ」（GitHub Actions 不採用）

- **状況**: 本番母艦はラズパイ 4B（aarch64・8GB・家庭内 LAN・外部公開しない＝[ADR-001](#adr-001-単一ユーザー認証なし)）。[ADR-021](#adr-021-開発本番ともコンテナdocker-compose-で動かす) で「**イメージは別 PC でクロスビルド → ラズパイは pull のみ**」と方針だけ決めていたが、配布経路（誰がどこでビルドし、どうラズパイへ届け、どう起動するか）が未実装だった。CI/CD として GitHub Actions を使うか検討した。
- **決定**: GitHub Actions は使わず、**開発機の Apple Silicon Mac を ADR-021 の「別 PC」に充て、ローカルで `linux/arm64` をネイティブビルド → `ghcr.io` に push → 同一 LAN のラズパイへ `ssh` で入って `compose pull → up` するデプロイスクリプト（`scripts/deploy.sh`・`make deploy`）**で配布する。
  - **ビルド**: `docker buildx --platform linux/arm64 --target prod/runner`。Mac(arm64)＝ラズパイ(aarch64) と**同アーキなのでネイティブビルド**（エミュレーション無し）。Dockerfile は `dev`/`prod`（backend）・`dev`/`runner`（frontend standalone）のマルチステージにし、dev の `compose.yaml` は `target: dev` を明示して本番と分離。
  - **配布**: `ghcr.io/rozurozu/assetvane-{backend,frontend}`。タグはイミュータブルな **`YYYYMMDD-HHMMSS`** ＋追従用 `latest`。トレース用に **jj の short change-id を OCI label**（`org.opencontainers.image.revision`）に焼く。`compose.prod.yaml` は `${IMAGE_TAG}` を参照し、**ロールバックは `IMAGE_TAG` を前値に戻して `up -d`**（ラズパイの `.last_good_tag` に直近正常タグを記録）。
  - **デプロイ手順**: `ssh` で ① **デプロイ前バックアップ**（旧コンテナで `VACUUM INTO`＝[ADR-017](#adr-017-sqlite-を定期バックアップする)・`app.scripts.backup`）→ ② `compose pull` → ③ `up -d`（FastAPI 起動で `alembic upgrade head` が自動実行）→ ④ `/health` ポーリング → ⑤ 失敗なら `.last_good_tag` で自動ロールバック。down→up の一瞬の停止は許容（自分専用ダッシュボード・ゼロダウンタイム不要）。
  - **`NEXT_PUBLIC_API_BASE_URL` はビルド時に bundle へ焼き込む**（Next の仕様）。`scripts/deploy.sh` が build-arg で本番 URL（`http://raspberrypi.local:8000`）を渡す（[architecture.md §7.1](architecture.md) の落とし穴）。
- **理由**:
  - **GHA のクラウドランナーは x86** で、`linux/arm64` を焼くには QEMU エミュが要り、`lightgbm`/`cvxpy`/`pandas` 等のネイティブビルドが重く失敗しやすい（[ADR-021](#adr-021-開発本番ともコンテナdocker-compose-で動かす) が警告する罠を自ら踏みに行く形）。**Apple Silicon Mac ならネイティブで速く確実**。
  - **クラウドは家庭内 LAN のラズパイに到達できない**（外部非公開＝[ADR-001](#adr-001-単一ユーザー認証なし)）。ビルド機が LAN 内の Mac なら `ssh` 直結で済み、self-hosted runner も VPN も watchtower も要らない（接続は Mac→Pi の LAN 内のみ・inbound を一切開けない）。
  - **DB アプリなのでデプロイに `alembic upgrade head` と事前バックアップが死活的**。スクリプトなら手順に組み込めるが、watchtower 等の自動更新では migration が抜ける。
  - 単一ユーザーの個人プロジェクトで、デプロイは低頻度・手動トリガで足りる。GHA の常時 CI はオーバーキル。
- **代替案**:
  - **GHA でクラウドビルド→ghcr→ラズパイ pull** → QEMU エミュの遅さ・脆さ＋クラウドがラズパイに届かない二重苦で不採用。
  - **watchtower で自動更新** → migration を打てず DB アプリに不適。制御も弱い。不採用。
  - **Tailscale 等 VPN でクラウドから push** → 同 LAN の Mac があれば VPN 常設・鍵管理が無駄に増える。不採用。
  - **`docker save | ssh load` 直送（レジストリなし）** → タグ履歴が無くロールバックが手作業になる。ghcr 経由を採用（private package なのでラズパイで初回 `docker login ghcr.io`＝read:packages PAT が要る）。
  - **CI（pytest/ruff/biome ゲート）が欲しくなったら**、ビルド/デプロイとは切り離して GHA に後付けできる（本 ADR はそれを禁じない）。
- **詳細**: `scripts/deploy.sh`（build→push→ssh デプロイ・ロールバック）・`Makefile`（`make deploy`）・`compose.prod.yaml`（ghcr image・`restart: unless-stopped`・`DATABASE_PATH=/data/assetvane.db`・`BATCH_SCHEDULER_ENABLED=true`）・`backend/Dockerfile`（base→dev→prod）・`frontend/Dockerfile`（dev→deps→builder→runner・standalone）・`frontend/next.config.ts`（`output:"standalone"`）・`backend/app/scripts/backup.py`（VACUUM INTO・prune）・運用手順は [docs/deploy.md](deploy.md)。

## ADR-036: バッチは「停止できる・状態が見える」——実行状態はメモリ singleton・停止は協調キャンセル

- **状況**: 初回デプロイの全銘柄フルバックフィルは約100〜150分かかる（[ADR-008](#adr-008-j-quants-は-v2-を使う) の Free レート制約）。これを**自分の操作で起動**したいが、従来 `POST /batch/run` は `202 {started:true}` を返すだけで、(a) 走っているか・今どのジョブかを **WebUI から知る術が無く**、(b) 誤って起動したフルを**止める術も無かった**（BackgroundTask は強制キャンセルできない）。「動いているのが見える」「止められる」を最小コストで足したい。バッチ実行履歴を DB に持つ案（`batch_runs` テーブル）も検討した。
- **決定**: 実行状態を **FastAPI プロセス内のメモリ singleton**（`batch/state.py`・`running`/`current_job`/`started_at`/`full_backfill`/`stop_requested`）で持ち、`GET /batch/status` で読む。停止は **協調キャンセル**＝`POST /batch/stop` が `stop_requested` を立て、`run_nightly()` が**各ジョブの境界で**見て break する（**今のジョブを終えてから止まる**・強制 kill はしない）。中断は意図的操作なので「正常終了」扱いとし、**Discord エラー通知は鳴らさない**。差分・フルどちらの走行でも効く。CLI 全銘柄フルは `make batch-full`（既存 `backfill --nightly` を口出し）。あわせて J-Quants 認証ピング（`check_jquants`・DB 非依存）を discord-test と同型の 3 口（CLI/REST/WebUI）で追加。
- **理由**:
  - **メモリ singleton で十分・DB スキーマを増やさない**: バッチは BackgroundTask（`/batch/run`）・APScheduler（cron）・CLI（`--nightly`）の**いずれも同一プロセス内**で走る（[ADR-005](#adr-005-db-に触れる-os-プロセスは-fastapi-だけ)）。プロセスが死ねば走行も状態も一緒に消えるので `running` の真偽が常に整合し、永続化が要らない。`batch_runs` テーブルは進捗・履歴が豊かだが、本件の要件（「動いているのが見える・止められる」）には過剰で、スキーマ・書き込み・[ADR-002](#adr-002-sqlite-wal) の書き手規律を増やす。履歴は既に Discord digest と `notifications` が担う。
  - **状態更新は `run_nightly()`（脳）の中だけ**で行うので、起動口（cron / REST / CLI）に依らず状態が映る（[ADR-011](#adr-011-1-つの脳複数の起動口)「1つの脳」に素直）。
  - **協調キャンセルが唯一安全な停止**: BackgroundTask は強制終了できない。ジョブ境界で `stop_requested` を見る方式なら、UPSERT の途中で切らず**ジョブの冪等性を壊さない**（[ADR-002](#adr-002-sqlite-wal)）。「今のジョブ完了後に止まる」は初回フル誤起動の保険として実用十分。
  - **フル起動は WebUI でチェックボックス＋確認ダイアログ**にゲートする（差分が日常の主役・フルは稀で重い）。空 DB では差分も自己修復で full 相当になるが、部分実行からの復旧では明示フルが要る（`fetch_meta` の中途半端な前進で歴史に穴が空くのを埋める）。
- **代替案**:
  - **flock ポーリングだけ**（`/batch/status` で try-acquire して boolean を返す）→ スキーマ最小だが「今どのジョブ」「停止」が作れず、結局作り直しになる。不採用。
  - **`batch_runs` テーブルで履歴永続化** → 進捗・ジョブ別結果・履歴が豊かだが、本件にはオーバー。将来ダッシュボードが要れば足せる（本 ADR は禁じない）。
  - **強制 kill（スレッド中断）** → UPSERT 途中で切れてジョブ冪等性を壊す危険。不採用。
- **詳細**: `backend/app/batch/state.py`（メモリ状態）・`batch/runner.py`（境界で `should_stop`・停止は通知なし）・`routers/batch.py`（`/batch/status`・`/batch/stop`）・`services/diagnostics.py`＋`routers/diagnostics.py`＋`scripts/jquants_test.py`（J-Quants 疎通 3 口）・`Makefile`（`make batch-full`/`jquants-test`）・`frontend/src/app/settings/page.tsx`（フルチェックボックス＋確認・進捗ポーリング＋停止・疎通ボタン）。

## ADR-037: Next ↔ FastAPI は同一オリジン化（Next rewrites プロキシ）——CORS と API_URL 焼き込みを廃止

- **状況**: 旧構成はブラウザが backend(:8000) を**直接** cross-origin で叩いていた。代償として (a) backend に **CORS 許可オリジン**（`CORS_ALLOW_ORIGINS`）が要り、(b) frontend に **backend のホストを `NEXT_PUBLIC_API_BASE_URL` でビルド時焼き込み**する必要があった。ラズパイ運用では「ブラウザで開く URL のホスト」「焼き込んだ API_URL のホスト」「backend の CORS のホスト」の**3 つが一致**しないと画面に「backend 未接続」が出る地雷があり（IP/mDNS の取り違え・DHCP で IP 変動で再発）、実際に踏んだ。「CORS と焼き込みの設定自体を無くしたい」が動機。
- **決定**: frontend が API を**相対パス `/api`** で叩き（`lib/api.ts` の `API_BASE = "/api"`）、**Next の rewrites**（`next.config.ts` の `/api/:path*` → `${BACKEND_ORIGIN}/:path*`）が裏で backend へ素通しする**同一オリジン化**にする。ブラウザの相手は常に frontend(:3000) だけになるので、**backend の CORSMiddleware・`CORS_ALLOW_ORIGINS`・`cors_origins` を全廃**し、**`NEXT_PUBLIC_API_BASE_URL` の焼き込み（deploy の `API_URL` build-arg）も全廃**する。rewrites の転送先は環境変数 `BACKEND_ORIGIN`（既定 `http://backend:8000`・ホスト直 dev のみ `http://localhost:8000`）。
- **理由**:
  - **3 ホスト一致地雷を根本から消す**: ブラウザが backend のホストを知らなくなる。転送先は **Docker 内部 DNS の固定名 `backend:8000`**＝ホスト非依存なので、Pi の IP/mDNS が何であろうと**同じイメージ・無設定**で動く。DHCP で IP が変わっても再発しない。
  - **設定が純減する**: CORS 設定（backend `.env`）と API_URL 焼き込み（`deploy.env`/build-arg）という**2 つの env が丸ごと消える**。デプロイは `make deploy` に Pi ごとの URL を渡す必要が無くなった。
  - **[ADR-005](#adr-005-db-に触れる-os-プロセスは-fastapi-だけ) を侵さない**: rewrites は**透過 HTTP プロキシ**で、Next は DB を触らず REST を素通しするだけ。「DB に触れるのは FastAPI だけ・Next は UI 専用でデータは REST 経由」を保つ。
  - **既存イメージ構成と相性が良い**: frontend は既に standalone の Node サーバ（`server.js`）が常駐しており（[ADR-021](#adr-021)）、rewrites はその上で動く。追加コンテナ（別建てリバースプロキシ）は不要。
- **代替案**:
  - **Caddy 等の前段リバースプロキシ**を 1 コンテナ足して frontend と `/api` を同一ポート配信 → 同一オリジン化は達成できるが構成が増える。既存 Next サーバで足りるので不採用。
  - **CORS を `*` 許可＋焼き込みは IP 固定**で運用 → 地雷（焼き込みの 3 ホスト一致・DHCP 変動）が残るので不採用。
  - **チャットのストリーミング懸念**: 現状チャットは非ストリーミング fetch（SSE 不使用）なので rewrites 越しで問題なし。将来 SSE 化する場合も Next rewrites はストリーミングを通すが、その時に実機検証する。
- **詳細**: `frontend/next.config.ts`（`rewrites()`・`BACKEND_ORIGIN`）・`frontend/src/lib/api.ts`（`API_BASE="/api"`）・`frontend/Dockerfile`（build-arg を `BACKEND_ORIGIN` に）・`compose.yaml`／`compose.prod.yaml`（frontend env）・`scripts/deploy.sh`＋`deploy.env.example`（`API_URL` 廃止）・`backend/app/main.py`（CORSMiddleware 撤去）・`backend/app/config.py`（`cors_*` 撤去）・`backend/.env.example`。

## ADR-038: ログ規約——テキスト形式・stdout 集約・docker json-file ローテーション・握り潰し禁止

- **状況**: ラズパイの「画面に『backend 未接続』バッジが出る」障害（[ADR-037](#adr-037-next--fastapi-は同一オリジン化next-rewrites-プロキシcors-と-api_url-焼き込みを廃止) の 3 ホスト一致地雷）を追ったとき、**ログがほぼ無く原因を追えなかった**。frontend は失敗を `.catch` で握り潰し、backend のログ方針も曖昧（標準 `logging` を使うとだけ決め・[ADR-018](#adr-018-無人運用の障害時方針失敗を黙って放置しない)）で、フォーマット・レベル・出力先・Pi での永続化が未定義だった。無人運用（[ADR-018](#adr-018-無人運用の障害時方針失敗を黙って放置しない)）の「静かな失敗」を可観測にしたい。
- **決定**: ログ基盤を以下に固定する。
  - **標準 `logging` ＋ `logging.config.dictConfig`** で構成し、**人間可読のテキスト形式**（`%(asctime)s %(levelname)s %(name)s: %(message)s`）にする。JSON 構造化ログへの集約は将来 Mac mini 等のログ集約基盤を入れる時に再検討する（単一ユーザー・1 ホストの今は人間が `docker logs` を直に読む方が速い）。
  - **レベルは `LOG_LEVEL` env で root を可変**（既定 `INFO`・`backend/app/config.py` の `log_level`）。設定の所在は **`backend/app/logging_config.py:setup_logging()`** に一元化し、**`backend/app/main.py` が import 時に呼ぶ**（uvicorn ロガーと整合させ、`/health` の access ログは抑制する）。
  - **出力は stdout/stderr に寄せる**。**Pi での永続化は docker の json-file ローテーション**（`max-size: 10m` × `max-file: 5`＝1 サービスあたり最大 50MB で頭打ち）を `compose.yaml`／`compose.prod.yaml` の各サービスに設定する。**アプリ側で FileHandler は使わない**——ファイルを持つのは FastAPI のプロセスが扱う DB だけ（[ADR-005](#adr-005-db-に触れるのは-fastapi-のみnext-は-rest-経由)）という規律に揃え、ログファイルの二重管理（ローテーション・退避・パーミッション）を避けるため。
  - **失敗・警告はコンテキストを最も持つ層で 1 度だけ**出す（log-and-rethrow を各層で重ねない）。**`.catch`／`except` での握り潰しは禁止**（frontend 含む）。今回の `Topbar` が実例で、ヘルスチェック失敗を黙殺せず `console.error` ＋定期再チェックにした（[ADR-037](#adr-037-next--fastapi-は同一オリジン化next-rewrites-プロキシcors-と-api_url-焼き込みを廃止) の罠を二度踏まないため）。
  - **`/health` の access ログは抑制**する。定期ヘルスチェック（フロントの周期 fetch・compose の healthcheck 等）で本当に見たいログが埋もれるのを防ぐ。
  - **無人バッチの失敗は Discord 通知**（[ADR-007](#adr-007-通知は-discord-webhookline-notify-は不採用)／[ADR-018](#adr-018-無人運用の障害時方針失敗を黙って放置しない) を踏襲）。ログとは**別経路**で、ログを読みに行かなくても気づけるようにする。
- **理由**:
  - **テキスト＋stdout が今の規模に最適**: 読み手は自分 1 人・ホストは Pi 1 台で、`docker compose logs -f` を直に読むのが最速。JSON 化や集約 SaaS はオーバーキルで、必要になってから足せる。
  - **stdout に寄せれば docker がローテーションを担う**: アプリが FileHandler を持つと、ローテーション・サイズ上限・退避を自前で管理することになり [ADR-005](#adr-005-db-に触れるのは-fastapi-のみnext-は-rest-経由)（ファイルを持つのは DB＝FastAPI だけ）と二重管理になる。docker json-file に寄せれば SD カードの I/O・寿命（[ADR-017](#adr-017-sqlite-を定期バックアップする)）も上限 50MB で守れる。
  - **握り潰し禁止が今回の障害の本質**: ログが無かったのではなく、失敗が握り潰されていた。`.catch`／`except` で握って後続を進めてよいのは夜間バッチのジョブ単位（個別失敗を握って後続を止めない＝[ADR-018](#adr-018-無人運用の障害時方針失敗を黙って放置しない)）など**意図を明記した箇所だけ**で、それ以外は最もコンテキストを持つ層で 1 度だけ出す。
- **代替案**:
  - **JSON 構造化ログ＋集約（Loki/ELK 等）** → 単一ユーザー・1 ホストには過剰。Mac mini 導入時に再検討（本 ADR は禁じない）。
  - **アプリ側 FileHandler ＋ `RotatingFileHandler`** → [ADR-005](#adr-005-db-に触れるのは-fastapi-のみnext-は-rest-経由) と二重管理になり、docker ローテーションと役割が重複。不採用。
  - **ログレベルをコード固定** → 障害解析時に DEBUG へ上げられないと不便。`LOG_LEVEL` env で可変にする。
- **関連**: [ADR-005](#adr-005-db-に触れるのは-fastapi-のみnext-は-rest-経由)（ファイルを持つのは FastAPI だけ）・[ADR-007](#adr-007-通知は-discord-webhookline-notify-は不採用)／[ADR-018](#adr-018-無人運用の障害時方針失敗を黙って放置しない)（無人失敗は Discord・別経路）・[ADR-017](#adr-017-sqlite-を定期バックアップする)（SD I/O・寿命）・[ADR-037](#adr-037-next--fastapi-は同一オリジン化next-rewrites-プロキシcors-と-api_url-焼き込みを廃止)（今回の障害の発端）。
- **詳細**: `backend/app/logging_config.py`（`setup_logging()`・`dictConfig`・uvicorn 整合・`/health` 抑制）・`backend/app/main.py`（import 時に `setup_logging()`）・`backend/app/config.py`（`log_level`・env `LOG_LEVEL`）・`compose.yaml`／`compose.prod.yaml`（json-file ローテーション 10m×5）・`frontend/src/components/shell/Topbar.tsx`（握り潰し廃止・定期再チェック）・`frontend/src/lib/api.ts`（失敗時に解決済み URL を載せる・`getHealth` タイムアウト）・運用は [docs/deploy.md](deploy.md) の「ログの見方」。

---

## ADR-039: Phase 7 を (A) Sector Lead-Lag 先行／(B) 米株拡張に分割し、(A) の業種 ETF は IndexAdapter に Yahoo ソースを足して流用する

- **状況**: [roadmap.md Phase 7](roadmap.md) は「Sector Lead-Lag（日米業種リードラグ・論文 SIG-FIN-036 ベース）」と「米国株拡張（米株スクリーナー `/us-stocks`・米国個別株 OHLCV・通貨/FX 換算）」という**性質の違う 2 成果物**を 1 つの Phase に束ねていた。前者は「日足の終値だけで動く軽量な提示専用シグナル」、後者は「数千銘柄の OHLCV・財務ソース・通貨列の波及（holdings/cash/asset_snapshots）・`UsEquityAdapter` 新設」という重い基盤拡張で、リスクと所要が桁違いに違う。これを 1 つのまま進めると、軽い前者まで重い後者に引きずられて止まる。grill-me（`snappy-cuddling-scott`）で分割を確定した。あわせて grill 中に**既存バグが判明**＝指数取得の `StooqIndexSource` が Stooq の BOT 判定で死に、`index_quotes` の取得（^SPX 等）が現状壊れている。
- **決定**:
  - **Phase 7 を (A)／(B) に分割し、(A) Sector Lead-Lag を先行**する。(B)〔米株スクリーナー `/us-stocks`・米国個別株 OHLCV・米国ファンダ源・`UsEquityAdapter` 新設・通貨列／`FxAdapter`／GICS／holdings·cash·asset_snapshots の通貨波及〕は別サブフェーズに**繰り延べ**る。
  - **(A) は論文 SIG-FIN-036（中川慧ほか, 人工知能学会, 2026）に忠実実装**する。米国業種 ETF の当日 close-to-close ショックを、事前部分空間へ正則化した PCA（低ランク予測器）に通して**翌営業日の日本業種スコア**を算出し、`signals`（`signal_type='lead_lag'`・JP 業種コードを `code`）に提示用の最新日だけ UPSERT する。**提示専用**で自動売買はしない（[ADR-009](#adr-009-自動売買はしない提示に徹する)と整合）。手法本体は**テスト済み純関数 `quant/lead_lag.py`**（`compute_lead_lag` ＋ `validate_lead_lag`・DB 非依存）に置く（[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け)／[ADR-016](#adr-016-手法はコードで実装する手法db-は索引でありコードの代替ではない)）。確定パラメータはモジュール定数（`L=60`, `K0=3`, `K=3`, `λ=0.9`, `q=0.3`・env 不可＝[ADR-027](#adr-027-手法パラメータは-phase-1-はコード定数将来-method_settingsai-は助言自動改変しない) 流）。
  - **【ロードマップ逸脱・本 ADR の核】米国業種 ETF（11 本の SPDR）の取得は、roadmap が想定した `UsEquityAdapter` ではなく、既存の [`IndexAdapter`](architecture.md)（フォールバック連鎖ファサード・[ADR-010](#adr-010-データソースはアダプタ越しにする)）に新ソース `YahooIndexSource`（yfinance・`auto_adjust=True`）を足して `index_quotes` に流用する**。Yahoo を主・Stooq をフォールバックにする（`settings.index_sources="yahoo,stooq"`）。日本側 TOPIX-17 業種 ETF（1617〜1633）は既存 `daily_quotes`（J-Quants）を使う。
  - **Free プラン時はハード無効化しない**。Free は株価が約 12 週間遅延し、リードラグの「翌日予測」のシグナル日付が約 3 ヶ月古くなって実用外になるが、計算は出した上で frontend に**目立つ低信頼バナー**を出す（`meta.plan` / `meta.is_delayed` / `meta.model_as_of` で判定。Light プランなら本来機能する）。
  - **検証は軽量にとどめる**。履歴で各 t のシグナルと実現リターンの Spearman IC、および 3 分位ロングショート（q=0.3）の R/R・方向的中率を numpy＋pandas のみで算出し、`meta` に同梱する。**Fama-French / Carhart 回帰・フル backtest 基盤は対象外**。
  - **提示/AI 配線**: 専用ページは作らず、Dashboard ウィジェット（`GeneralNewsWidget` 流用の `LeadLagWidget`）＋ AI Tool `get_lead_lag`（`min_phase=7`・軸1/軸2 共用）＋ `signals` 統合＋ `GET /lead-lag`。`CURRENT_PHASE` を 4→7 に上げる。マイグレーション不要（`signals`・`index_quotes` を流用）。
- **理由**:
  - **(A) は軽く・(B) は重い**。(A) は日足終値のみ・通貨/FX 不要・新テーブル不要で、ラズパイ夜間バッチに無理なく載る。(B) を待たずに価値（提示材料）を出せるので先行が合理的。
  - **業種 ETF を `IndexAdapter` に流用する理由（最小変更）**: リードラグに必要なのは**業種 ETF の調整後終値だけ**で、OHLCV も通貨換算も要らない。これは既存 `index_quotes`（指数の水準を日足で持つ）の住所にちょうど収まる。`UsEquityAdapter` は OHLCV・財務・通貨という (B) の重い関心を背負うアダプタで、(A) のためだけに前倒し新設すると **(B) のスコープが (A) に漏れて膨張**する。
  - **同時に既存バグも復旧する**: `YahooIndexSource` を主ソースに足すことで、Stooq 障害で死んでいた ^SPX 等の指数取得も生き返る（フォールバック連鎖の先頭に生きたソースが入る）。1 つの追加で「(A) の取得」と「既存指数取得の復旧」を同時に解く。
  - **Free でハード無効化しない理由**: 開発は Free（[ADR-008](#adr-008-j-quants-は-v2-を使う)）で進む。計算経路は本番と同じに保ちつつ「いま見ている数字は約 3 ヶ月前のもの」と明示する方が、無効化して何も見せないより開発・検証に有用（Light に上げれば即実用になる）。
- **代替案**:
  - **`UsEquityAdapter` を前倒し新設して業種 ETF を取る** → (B) の重い関心（OHLCV・通貨・財務源）を (A) に持ち込み、(A) のスコープが膨張する。却下。
  - **`StooqIndexSource` 単一を継続** → そもそも現状 Stooq が BOT 判定で死んでおり取得不能。却下（フォールバック連鎖に残置はするが主ソースにはしない）。
  - **キー付きの契約データソース（有償 API）を主にする** → Phase 7(A) の提示用途には過剰でコストもかかる。将来 (B) で精度・銘柄数が要るときにフォールバック連鎖へ足せばよい（[ADR-010](#adr-010-データソースはアダプタ越しにする) の連鎖は後付け可能）。保留。
  - **Phase 7 を分割せず一括実装** → (B) の重さに (A) が引きずられる。却下（分割が本 ADR の主旨）。
  - **scipy を入れて固有分解する** → numpy（`eigh`）＋pandas で足り、依存を増やさない。不採用（追加依存は yfinance のみ）。
- **(B) への繰り延べ事項（明記）**: 米株スクリーナー `/us-stocks`（[ADR-031](#adr-031-株式スクリーナー夜間-valuation_snapshots-読み取り時ランク市場ごとに分離)）・米国個別株（数千・OHLCV・`UsEquityAdapter` 新設）・米国ファンダ源・通貨列・`FxAdapter`・holdings/cash/asset_snapshots の通貨/FX 波及・GICS 分類。これらは (A) のスコープ外で、(B) サブフェーズに送る。
- **段階**: **着工（2026-06-07）**。設計確定（grill `snappy-cuddling-scott`・論文 PDF `2026_76.pdf` 読了）。実装は `quant/lead_lag.py`（純関数）・`adapters/index.py`（`YahooIndexSource` 追加・yfinance 依存）・`services/lead_lag.py`・`batch/jobs/calc_lead_lag.py`（`NIGHTLY_JOBS` の `calc_signals` 後・`run_advisor` 前）・`GET /lead-lag` ＋ Tool `get_lead_lag`・frontend `LeadLagWidget`。手法カードは [docs/methods/lead-lag.md](methods/lead-lag.md)（参照知識・リポジトリ markdown）。
- **関連**: [ADR-009](#adr-009-自動売買はしない提示に徹する)（提示専用）・[ADR-010](#adr-010-データソースはアダプタ越しにする)（アダプタ越し・フォールバック連鎖）・[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け)／[ADR-016](#adr-016-手法はコードで実装する手法db-は索引でありコードの代替ではない)（手法はテスト済みコード）・[ADR-008](#adr-008-j-quants-は-v2-を使う)（Free 遅延）・[roadmap.md Phase 7](roadmap.md)・[advisor.md §5](advisor.md)・[docs/methods/lead-lag.md](methods/lead-lag.md)。

## ADR-040: ^TPX（TOPIX 指数）は J-Quants `/v2/indices/bars/daily/topix` を一次ソースに（IndexAdapter 連鎖に JQuantsIndexSource を後段追加）

- **状況/問題**: `IndexAdapter`（フォールバック連鎖・[ADR-038](#adr-038-ログ規約テキスト形式stdout-集約docker-json-file-ローテーション握り潰し禁止)／[ADR-039](#adr-039-phase-7-を-a-sector-lead-lag-先行b-米株拡張に分割し-a-の業種-etf-は-indexadapter-に-yahoo-ソースを足して流用する)）で ^TPX（TOPIX 指数水準）を取りにいくと、**Yahoo に有効なシンボルが無く、Stooq でも取得できない**ため、毎晩の `fetch_index` が ^TPX だけ失敗し続け、その都度 Discord に警報が鳴っていた。連鎖の全ソースが ^TPX を返せないのが根因で、ログ/通知のレイヤでは解決しない。
- **検討して却下した案**:
  - **① allowlist で ^TPX の失敗を警報対象から外す（黙らせる）** → **却下**。通知の目的は「取れていない＝直せ」を知らせることで、それを握り潰すのは本末転倒。取得経路を直さないまま警報だけ消すと、他シンボルの本物の障害も気づけなくなる（[ADR-038](#adr-038-ログ規約テキスト形式stdout-集約docker-json-file-ローテーション握り潰し禁止) の「握り潰し禁止」と同じ規律）。
  - **② TOPIX 連動 ETF（1306 等）の価格で代用する** → **却下**。ETF 価格は指数とスケールがズレ（運用コスト・基準価額・分配の影響）、欲しいのは**真の指数水準**そのもの。代用すると以後の比較・シグナルが指数水準前提から崩れる。
- **決定**: 正攻法で**「取れるソースを足す」**。J-Quants 自身の TOPIX 指数 API（`GET /v2/indices/bars/daily/topix`・レスポンス `Date/O/H/L/C`＋`pagination_key`）を `JQuantsIndexSource`（^TPX 専用）として `IndexAdapter` のフォールバック連鎖に**後段追加**する（`yahoo,stooq` の後ろ）。^NKX・米指数は従来どおり連鎖前段の別ソース（Yahoo 等）で取り、^TPX のみ本ソースが拾う。データソースはアダプタ越し（[ADR-010](#adr-010-データソースはアダプタ越しにする)）・認証は V2 の `x-api-key`（[ADR-008](#adr-008-j-quants-は-v2-を使う)）に従う。
- **制約/現状**: TOPIX 指数 API は **Light 以上**で、**Free では 403**（`{"message":"This API is not available on your subscription. Please consider a subscription upgrade."}`）。当面 Free 据え置きのため、**Free では `JQuantsIndexSource` は弾込めのみ**（403 を返すだけで実データは流れない）で、**Light に上げた瞬間に ^TPX が連鎖から流れ出す**。Free 期間は ^TPX の取得失敗が log/Discord に残るのは**許容**する＝「Light に上げろ」のナッジとして機能させる（① の allowlist で黙らせない判断と一貫）。
- **理由**:
  - **根因を直す**: 連鎖の全ソースが返せないなら、返せるソースを足すのが筋。J-Quants は既に日本株で認証・スロットルが通っており（[ADR-008](#adr-008-j-quants-は-v2-を使う)）、^TPX 専用ソースを 1 本足すだけで連鎖が成立する（[ADR-010](#adr-010-データソースはアダプタ越しにする) の連鎖は後付け可能）。
  - **指数水準が欲しい用途に忠実**: ETF 代用ではなく指数 API を使うことで、以後の比較・提示が「真の TOPIX 水準」前提で一貫する。
  - **Free でハード無効化しない**: 計算/取得経路は本番（Light）と同じに保ち、Free では 403 が log/Discord に出ることでアップグレードを促す。無効化して経路ごと隠すより、開発・運用の見通しが良い（[ADR-039](#adr-039-phase-7-を-a-sector-lead-lag-先行b-米株拡張に分割し-a-の業種-etf-は-indexadapter-に-yahoo-ソースを足して流用する) の Free 非無効化方針と同じ姿勢）。
- **段階**: docs 確定（2026-06-07・実 API プローブ＋公式 spec で検証）。コード実装は別タスク（backend 担当）で `adapters/index.py` に `JQuantsIndexSource` を追加・連鎖末尾に組み込む。詳細は [jquants.md §6 項目7](jquants.md) を参照。
- **関連**: [ADR-008](#adr-008-j-quants-は-v2-を使う)（J-Quants V2 認証・Free/Light）・[ADR-010](#adr-010-データソースはアダプタ越しにする)（アダプタ越し）・[ADR-038](#adr-038-ログ規約テキスト形式stdout-集約docker-json-file-ローテーション握り潰し禁止)（握り潰し禁止）・[ADR-039](#adr-039-phase-7-を-a-sector-lead-lag-先行b-米株拡張に分割し-a-の業種-etf-は-indexadapter-に-yahoo-ソースを足して流用する)（IndexAdapter フォールバック連鎖）・[jquants.md](jquants.md)。
