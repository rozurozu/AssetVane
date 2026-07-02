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
- **補足（スロットル間隔はプラン名で持つ・2026-06-06）**: レート制限（Free 5 / Light 60 req/分）に対するスロットル間隔は、**env で秒数を直接お守りせず `JQUANTS_PLAN`（`free`/`light`）というプラン名 1 語で指定**し、秒数はコード内マッピング（`adapters/jquants.py` の `_PLAN_INTERVALS`＝`free`→16s / `light`→1s）が決める。**V2 に契約プランを返す API は無い**（公式 rate-limits・V2 エンドポイント一覧で確認・`X-RateLimit` ヘッダも無し）ため自動検出はできず、env でプラン名を渡す。`free`=16s は本番投入の実測根拠あり（[jquants.md §4](jquants.md)）、`light`=1s は目安（実運用で要実測）。`standard`/`premium` は未実測のため未収載（必要時に実測して足す）。未知プラン名は `free`（最も遅い＝最安全）に倒し warning を出す（typo で速くしすぎてブロックを誘発しない）。プラン移行は実運用時にこの 1 語を変えるだけ（秒数のコード変更は不要）。**→ 後に [ADR-061](#adr-061-j-quants-の-api-キーとプランを-env-から-dbwebuisettings-へ移管する) で `JQUANTS_PLAN`/`JQUANTS_API_KEY` は env から DB（`jquants_config`）＋`/settings` へ移管した（2026-06-24）。プラン名 1 語で秒数を決める設計（`_PLAN_INTERVALS`・free/light/standard/premium）はそのまま、保存先が env→DB に変わっただけ。**

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
- **補足（将来＝多モデル登録の予約）**: 現状は `.env` で 1 モデルを差し替える。将来、複数モデルを登録し**タスク別にルーティング**（相談・Tool Calling は強モデル／ニュース要約・テーマ語彙照合〔[ADR-050](#adr-050-銘柄とニュースにテーマタグを持たせ語彙揺れをプロンプト照合と-embedding-近接で抑える)〕等の軽量タスクは弱モデル）する拡張余地を予約する。**今回は決定にしない**（かなり後段のスコープ）。
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
- **決定**: 開発・本番とも **Docker Compose** で動かす。backend・frontend を各コンテナにし、**SQLite はファイルなので DB コンテナは作らず**ボリュームで永続化する（dev=named volume `assetvane-db`／prod=bind mount `./data:/data`。当初この文面は「named volume（`data/`）」と曖昧で dev の実体は bind mount だったが、2026-06-22 の DB 破損を機に [ADR-060](#adr-060-dev-の-sqlite-は-named-volume-に載せるmacos-docker-desktop-の-bind-mount-では-walmmap-が壊れるためprod-は-bind-mount-維持) で dev/prod の置き場を確定した）。
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
- **理由**: 「状態の連続性」（[ADR-011](#adr-011-ai-advisor-を-2-軸夜の分析ai相談チャットaiで実装する製品の核心)）を画面体験として実現する。root layout 配置はフレームワーク標準の素直な方法で、特別な仕掛けが要らない。
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

- **状況**: 当初の Phase 1 設計は「検知条件（ゴールデンクロス成立・出来高 3 倍超）を満たした行だけ `signals` に保存し、一覧にスコア順で出す」だった。だが利用者の本来の使い方は「**シグナル一覧を人が見て判断する**」のではなく「**AI Advisor が複数の手法で銘柄を確認し、根拠つきで提案する**」（[ADR-011](#adr-011-ai-advisor-を-2-軸夜の分析ai相談チャットaiで実装する製品の核心)・[advisor.md](advisor.md)）。この前提だと、閾値で「クロス目前(near-miss)だが他のシグナルは強い」銘柄を**保存時にバッサリ捨てる**と、AI の判断材料が消える。
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
  - **月額しきい値の既定 = $50**。挙動は **3 値トグル**: `off`（監視しない）/ `warn`（**既定**・LLM 呼び出しは止めず Discord 通知＝ADR-007 ＋画面バナーで知らせる）/ `block`（しきい値超過で LLM 呼び出しを止める）。✅ **実装済み（2026-06-08・cdbfa24）**: warn 時の Discord 通知＋画面バナーを接続済み（旧 `logger.warning` 止まりの `advisor/llm.py` TODO を解消）。夜間 `notify_cost_warn` が warn 超過時に `send_once("llm_cost_warn:<UTC 年月>")` で月 1 通の Discord 通知（即時性は advisor に持たせず batch にトリガを置く＝advisor→batch 逆流回避）。即時の気づきは `/health` の `llm_cost{mode,limit_usd,month_total_usd,exceeded}` を `Topbar` が読み、`mode=warn` かつ `exceeded` のとき warn 帯バナーを出す（追加ポーリングなし・`block` は既存経路）。
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

> **⚠️ Superseded by [ADR-073](#adr-073-codex-接続の撤去adr-032-を-superseded).** codex を AI Advisor の LLM プロバイダとして使う経路は 2026-07-02 に全撤去した（コード・env・Dockerfile 同梱バイナリ・`/mcp`・`/llm/codex/test` を削除）。以下は当時の記録として残す。現行は OpenAI 互換 provider 一本（[ADR-012](#adr-012-llm-はアダプタで抽象化openrouter-既定ローカルへ差替可)）。

- **状況**: AI Advisor は OpenAI 互換 API（OpenRouter・[ADR-012](#adr-012-llm-はアダプタで抽象化openrouter-既定ローカルへ差替可)）固定だった。**コスト削減**のため codex CLI でも動かしたい（codex は ChatGPT サブスク認証＝API キー不要・限界費用ゼロ。このマシンは `~/.codex/auth.json` で login 済み）。ただし「今まで通り API でも動く」を必ず残す。codex は外部定義の function tool を注入する口を持たず、自前 Tool を渡す正規ルートは MCP のみ。
- **決定**:
  - **自前 Tool は MCP サーバ化**。FastAPI プロセス内に streamable HTTP の `/mcp` を立て、既存 `REGISTRY` を `openai_tools(phase)` と同集合で公開（`app/advisor/mcp_server.py`）。handler は FastAPI 内で動き **DB に触れるのは FastAPI だけ（[ADR-005](#adr-005-db-に触れるのは-fastapi-だけ)）** を保つ。codex は別プロセスで HTTP 越しに呼ぶだけ。
  - **面別 provider 切替**。`chat`/`nightly`/`dossier` を個別に `"openai"`（既定）/`"codex"` 選択（`settings.provider_for`・`app/advisor/engine.py` のディスパッチャ）。既定は全面 openai＝何も設定しなければ従来通り。openai 経路（`llm.py`/`service.py`）は無改修。
  - **codex 駆動は `codex app-server`（stdio JSON-RPC）**。当初 `codex exec` を計画したが、**exec は非対話で MCP ツール呼び出しが常にキャンセルされる既知リグレッション（openai/codex #16685・#24135）**で詰んだ（`--dangerously-bypass-approvals-and-sandbox` 無しでは MCP を呼べない）。app-server は `mcpServer/elicitation/request` をプログラムで accept でき、**危険フラグ不要・read-only サンドボックス維持**で MCP が通る（実機検証済み・codex-cli 0.136.0）。
  - **app-server の抱え方**（`app/advisor/codex_engine.py`）: プロセス内で **1 本だけ常駐シングルトン**として遅延 spawn（死んだら次回再起動）。ターンは **async ロックで直列化**（1 本の stdio に全イベントが混ざるため）。**1 turn = 新規 thread**（stateless・openai 経路と同じく毎回 CORE/POLICY＋全履歴を載せる）。**CORE は thread/start の `baseInstructions`（[ADR-015](#adr-015-システムプロンプトは不変-core可変-policy-に分離) の不変 base ペルソナ枠）・POLICY ほか system は `developerInstructions`**。**tool_runs は `item/completed`（mcpToolCall・server=assetvane）から再構成**（[ADR-025](#adr-025-画面コンテキスト注入は軽量ヒントのみ数値は渡さない)＝結果値は載せない）。usage は cost_usd=0 で計上（[ADR-028](#adr-028-llm-コストガードレール監視と上限3-値トグルenv-既定設定-ui-上書き)）。
  - **障害時は API へ自動フォールバックしない**（コスト削減の意図を裏切らない）。一過性（codexErrorInfo の serverOverloaded 等）は指数バックオフ再試行、恒久・タイムアウト・空応答は `CodexEngineError` → [ADR-018](#adr-018-無人バッチの失敗は握りつぶさず-discord-へ通知) 処理（chat=502／nightly=journal スキップ＋通知）。
- **理由**: MCP 化で「AI は計算しない（[ADR-014](#adr-014-ai-に数値を計算させない)）」「DB は FastAPI だけ（ADR-005）」を破らず codex に自前 Tool を渡せる。面別切替なら chat だけ codex に寄せて nightly は安全に openai 維持できる。app-server は exec の MCP 詰みを回避しつつサンドボックス・無危険フラグを保てる唯一の道。turn 毎 thread は openai の stateless 構造と完全一致しドリフトが無い。
- **代替案**: (A) `codex exec` → MCP キャンセルのリグレッションで不採用（本 ADR の主因）。(B) function tool を直接注入 → codex に口が無い。(C) stdio プロキシ MCP → DB を別プロセスに晒し ADR-005 違反、不要。(D) 障害時に openai へ自動フォールバック → コスト削減の意図に反し、どちらで答えたか不透明になるため不採用。
- **段階化**: **今回は chat=codex を実機検証して緑**。nightly/dossier の codex 化は配線のみ（既定 openai・未実証扱い）。無人 cron での ChatGPT トークン継続（8 日ルール）を実証してから寄せる。なお**テストは provider を openai に固定する**（`conftest.py` の `client` フィクスチャ）＝`.env` の codex 設定が漏れて実 app-server が起動し、モック素通り＋teardown で `Event loop is closed` になるのを防ぐ。codex 経路自体は `test_engine_dispatch.py`/`test_codex_engine.py` がモックで検証する。
- **Docker 同梱（2026-06-08 追加）**: codex は PATH 上の `codex` を spawn する前提だが、開発・本番とも Docker Compose で動かす（[ADR-021](#adr-021-開発本番ともコンテナdocker-composeで動かす)）ため、**codex バイナリを backend イメージの base ステージに焼く**（`backend/Dockerfile`・Node 不要の musl static・GitHub releases `rust-v0.137.0` ピン・`TARGETARCH` で arm64/x86_64 出し分け）。dev/prod 双方に乗るので**ホストに codex を入れなくても `docker compose` から使える**。`auth.json` はマウントで供給（dev=`${HOME}/.codex`／prod=`/opt/assetvane/.codex` → コンテナ `/root/.codex`）。これで**本番ラズパイでも codex を使える**。マウントは **read-write**＝codex がリフレッシュで `auth.json` を書き戻すのは正常動作なので許可する（手動編集を避ける趣旨の「上書き禁止」とは別。供給手順は [docs/deploy.md](deploy.md)）。失効は「毎晩実行でリフレッシュされ実用上は回る／放置すると失効しうる／失敗は [ADR-018](#adr-018-無人バッチの失敗は握りつぶさず-discord-へ通知) 通知で検知」が現状認識。
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
  - **消費先（両方）**: ① **Dashboard ウィジェット**（`GET /general-news` → `GeneralNewsWidget` がカテゴリ別に表示）。② **軸1（夜の分析 AI・[ADR-011](#adr-011-ai-advisor-を-2-軸夜の分析ai相談チャットaiで実装する製品の核心)）の briefing**＝新 Tool `get_general_news`（`min_phase=4`）を足し、`_NIGHTLY_INSTRUCTION` で取得を促す。Tool なので**軸2 チャットでも再利用**できる。Discord 通知には含めない（Phase 6 の領域）。
  - **副件（上げ忘れ修正）**: 本実装で `CURRENT_PHASE` を 3→4 に上げた。Phase 4 完了時に上げ忘れており、`min_phase=4` の既存 Tool（`get_dossier` / `investigate_stock` / `fetch_news`）がチャット・夜AI に露出していなかった（夜間 `investigate_dossier` ジョブは handler 直呼びで動いていたため気づきにくかった）。これにより Phase 4 Tool 群と新 `get_general_news` が両軸に露出する。
- **理由**: 個別銘柄ドシエ（[ADR-020](#adr-020-個別銘柄ドシエ定性ファンダ調査-1銘柄1レポートを更新し続ける)）と一般ニュースは**住所（紐づく対象）が根本的に違う**ため、同じ台帳に混ぜず別系統で持つのが正しい。
- **代替案**: `dossier_sources` を code FK 任意に緩めて一般ニュースも載せる → 台帳の住所が崩れるため不採用。`fetch_news` を汎用化して共用 → 銘柄専用シグネチャがぶれるため不採用（新メソッド追加に留めた）。日次総括 markdown を別テーブルに焼く 2 テーブル構成 → 消費先が「眺める＋文脈材料」だけなので YAGNI（必要になれば後付け）。カテゴリ定義を env 化 → 構造データの JSON 文字列化・`.env.example` 同期が煩雑なだけで益が無いため定数モジュールに。
- **段階**: **実装済み（2026-06-06）**。pytest green（adapter / repo / API / job の単体＋migration 回帰）。frontend は Dashboard widget まで配線（専用ページは作らない）。
- **後日（発展置換）**: `general_news` テーブルは [ADR-044](#adr-044-ニュースを統合コーパスと階層タグに集約し-get_news_context-で3層を必ず揃える)（2026-06-07 実装）で統合コーパス `news`（`level='market'`）に**発展置換**された（撤回ではない）。本 ADR の「別系統で持つ／本文は要約のみ／カテゴリ定義は定数モジュール／`fetch_general_news` は `run_advisor` 直前」の判断はそのまま `news` 上で踏襲される。
- **後続**: [ADR-044](#adr-044-ニュースを統合コーパスと階層タグに集約し-get_news_context-で3層を必ず揃える)が本 ADR を**発展的に置換**する＝`general_news` を「銘柄・セクター・市況・ユーザー入力」を束ねる統合ニュースコーパスへ拡張し、`category` を `level`＋`sector17_code` タグに一般化する。本 ADR の「別系統で持つ／本文は要約のみ／カテゴリは定数モジュール」の判断は ADR-044 でも踏襲する（「専用ページは作らない」だけは [ADR-047](#adr-047-統合ニュースページ-news-を新設する一覧と検索と貼付フォーム)で覆す）。

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
- **追補（2026-06-22）— 長尺ジョブは内部ループでも `should_stop` を見る**:
  - **問題**: 上の「今のジョブ完了後に止まる」は、当初の前提（1 ジョブ＝1 営業日や数百銘柄で短い）では実用十分だった。だが Phase 7(B-1) の `fetch_us_quotes` は**全 us_stocks（約 1 万銘柄）×スロットルを 1 ジョブ内ループで走査**し最大数時間かかる。`POST /batch/stop` で `stop_requested` は立つのに、ジョブ内ループは誰も `should_stop` を見ないため、`/settings` の停止が「停止待ち」のまま最大数時間固まった（2026-06-22 実機・`fetch_us_quotes` 差分実行を停止できず）。
  - **決定**: **全ユニバース走査の長尺ジョブは、最外ループの先頭でも `state.should_stop()` を見て break する**（ジョブ境界停止に加える二段構え）。break 後は「取れた分まで UPSERT 済み・カーソル（`fetch_meta`）前進済み」で**冪等に再開可能**（ADR-018）なので、中断しても歴史に穴は空かない。対象は `fetch_us_quotes`（バッチ境界）・`fetch_quotes`（営業日境界）・`fetch_us_fundamentals`（銘柄境界）。検知粒度はループ 1 単位（米株 quotes なら 1 バッチ＝`us_quotes_batch_size`≒数十秒）。夜天井 cap で数十分以内に収まるタガー/embed/巡回系には足さない（過剰）。
  - **理由**: 強制 kill を避ける協調キャンセルの方針（上記「協調キャンセルが唯一安全な停止」）は維持したまま、**長尺ジョブだけ停止の応答性を上げる**最小変更。ジョブ境界停止（runner）と内部ループ停止（ジョブ）は同じ `should_stop()` を見るので意味が一貫し、停止は「正常終了」扱い（通知なし）も変わらない。
  - **詳細**: `batch/jobs/fetch_us_quotes.py`・`fetch_quotes.py`・`fetch_us_fundamentals.py`（各最外ループ先頭の `should_stop` ＋ detail への「停止により中断」表示）。走行中ジョブを**今すぐ**止めたい場合は backend プロセス再起動（`docker compose restart backend`）が確実で、UPSERT 冪等＋named volume なので DB は壊れない。

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
- **(B) への繰り延べ事項（明記）**: 米株スクリーナー `/us-stocks`（[ADR-031](#adr-031-株式スクリーナー夜間-valuation_snapshots-読み取り時ランク市場ごとに分離)）・米国個別株（数千・OHLCV・`UsEquityAdapter` 新設）・米国ファンダ源・通貨列・`FxAdapter`・holdings/cash/asset_snapshots の通貨/FX 波及・GICS 分類。これらは (A) のスコープ外で、(B) サブフェーズに送る。**→ (B-1) は [ADR-055](#adr-055-米株スクリーナーphase-7b-1は-yfinance-一本gics-は-yahoo-infosector-の文字列保持提示専用で-jpy-資産評価コアに触れない) で解消（2026-06-09）。(B-2) FX/保有波及は [ADR-057](#adr-057-phase-7b-2fx-基盤米株保有管理資産概要合算を最小スコープで実装する) で解消（2026-06-11）。**
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

---

> ADR-041〜043 は、`~/Develop/dexter`（金融リサーチ自律エージェント）の調査（[dexter-research.md](dexter-research.md)・[dexter-harness.md](dexter-harness.md)）から AI Advisor に活きる設計を取り込む 3 件。スコープは設計判断（本 ADR）まで。実装は別タスク。

## ADR-041: AI Advisor の CORE に反追従規律とペルソナ層を明示する（リスク選好は POLICY のまま・職業アイデンティティのみ固定）

- **状況**: CORE プロンプト（`backend/app/advisor/core_prompt.md`）は CORE 5 要素（役割・方法論・規律・Tool の使い方・出力の型＝[advisor.md §2](advisor.md)）を持つが、**(1) 反追従（sycophancy 回避＝ユーザーの誤った思い込みに同調しない）の規律が無く**、**(2) ペルソナが箇条書きで、一貫した人格として embody されにくい**。投資アドバイザーにとって追従は致命的で、ユーザーの誤前提・隠れたリスクに同意すると損失へ直結する。dexter の `SOUL.md` が手本になる——一人称の価値観宣言・名前付きメンタルモデル（margin of safety / invert / circle of competence）・"accuracy over comfort"・"protecting your interests" を、システムプロンプト組み立て時に `## Identity` として注入し "embody this" で締める（[dexter-harness.md §2](dexter-harness.md)）。
- **決定**:
  - **ペルソナ層を `core_prompt.md` 内に新設する**（別ファイル化しない）。[ADR-015](#adr-015-システムプロンプトを不変-core--可変-policyに分離する専門性は-coretool手法カードに宿す) が「CORE ＝ `core_prompt.md` 単一」と規定しているため、過剰なファイル分割をせず同ファイル内の一人称「ペルソナ／職業哲学」セクションとして書く。チャットで書き換えない不変層（jj 版管理・意図的コミットのみ）であることは [ADR-015](#adr-015-システムプロンプトを不変-core--可変-policyに分離する専門性は-coretool手法カードに宿す) のまま。
  - **ペルソナに固定するのは「職業アイデンティティ」のみ**: 規律あるクオンツアナリストの姿勢／反追従／知的誠実（"分からない" と言える・circle of competence）／**ユーザーの利益を守る**／事実を集めてから見解を作る（合理化しない）・margin of safety・PER 単体で割安判断しない等の**方法論の哲学**／**提示専用＝決めるのはユーザー・売買は実行しない**（[ADR-001](#adr-001-単一ユーザー前提で作る)/[ADR-009](#adr-009-日米業種リードラグ戦略は-assetvane-の分析機能とする自動トレードツールに持ち込まない)）。これらは**ユーザーのリスク選好に依らず不変**な、専門家としての"ものの見方"。
  - **リスク選好は CORE に書かない（POLICY のまま）**: `risk_tolerance` / `no_leverage` / `target_cash_ratio` / `max_position_weight` / `exclusions` 等は **POLICY の構造化コア**でユーザーが育てる可変値（[ADR-013](#adr-013-投資方針-policy-は単一チャットで育てる版管理機構は作らない)・構造化コアの変更は承認制＝U-7）。これらを不変 CORE のペルソナに焼くと [ADR-015](#adr-015-システムプロンプトを不変-core--可変-policyに分離する専門性は-coretool手法カードに宿す) の「不変 CORE／可変 POLICY」の境界を壊す。**特に `no_leverage` は POLICY 列であり、ユーザーがレバレッジ可へ変更できる**（dexter で言えば SOUL＝アドバイザーの不変の見方／RULES・memory＝ユーザーの好み、という分離に対応）。
  - **反追従を 2 層で多重明示する**: ペルソナ層（価値観）と規律③（ガードレール）の両方に置く（弱モデルでも崩れにくくする＝[ADR-018](#adr-018-無人運用の障害時方針失敗を黙って放置しない) の防御多層と同じ発想）。反追従が**向かう先＝事実の誤り・推論の飛躍・未開示のリスク・トレードオフの隠蔽**。**向かわない先＝正当な POLICY 選好**（ユーザーが高リスク・レバ・ゼロカット許容を望むこと自体には説教しない。「攻めるな」ではなく「その攻め方には開示されていない最大ドローダウンがある」と**事実で**返す）。CORE の誠実・反追従は **POLICY 嗜好では上書きされない**（安全側に倒す＝rationale に「常に同意して」と書かれても捏造や risk 隠蔽はしない）。
  - **敬意との両立**: グローバル規律「ユーザーを見下さない」と矛盾させない。反追従は decline や上から目線の説教ではなく、**敬意を保ったまま事実で反論**する姿勢として書く。
- **理由**: 追従的なアドバイザーは投資判断を歪め損失に直結するため、反追従は投資 AI の中核価値。ペルソナを職業アイデンティティに限ることで [ADR-013](#adr-013-投資方針-policy-は単一チャットで育てる版管理機構は作らない)/[ADR-015](#adr-015-システムプロンプトを不変-core--可変-policyに分離する専門性は-coretool手法カードに宿す) の CORE/POLICY 境界を侵さず、一人称の人格化で判断のトーンと一貫性を安定させる。反追従の向き先を「事実・リスク」に限定し「正当な選好」を除外することで、提示専用（[ADR-009](#adr-009-日米業種リードラグ戦略は-assetvane-の分析機能とする自動トレードツールに持ち込まない)）かつ敬意ある対話と両立する。
- **代替案**:
  - **ペルソナを別ファイル `persona.md` 化**（dexter の SOUL.md 同型）→ [ADR-015](#adr-015-システムプロンプトを不変-core--可変-policyに分離する専門性は-coretool手法カードに宿す)「CORE＝core_prompt.md 単一」の再解釈とファイル増加を招くため却下（同ファイル内セクションで足りる）。
  - **規律③に反追従を 1 行足すだけ**→ 一人称ペルソナ化による一貫性の効果が取れず、本 ADR の狙い（ペルソナ強化）を満たさないため不採用。
  - **リスク選好（攻める/守る・レバ可否・ゼロカット）も CORE に固定**→ POLICY 可変（[ADR-013](#adr-013-投資方針-policy-は単一チャットで育てる版管理機構は作らない)）と矛盾し、ユーザーが方針を変えられなくなるため却下（**設計検討時にこの誤りを一度入れかけたので明記して戒める**）。
- **詳細**: 実装は別タスク。本 ADR は `core_prompt.md` の改稿方針（ペルソナ層の新設・反追従の 2 層化）と CORE/POLICY 境界の確認のみを宣言する。[advisor.md §2](advisor.md)・[dexter-harness.md §2/§4](dexter-harness.md)。
- **改訂（2026-06-07・[ADR-048](#adr-048-銘柄バリュエーション判断基準roeperpbrを-tool事実参照知識カードで持たせる)）**: 当初「日本市場の作法（`Applied to the Japanese market` 節）は `core_prompt.md` 内に書く」としたが、**日本市場の"知識"部分（TSE 低 PBR 改革・政策保有解消・ガバナンス改革・脱デフレ）は参照知識（[ADR-016](#adr-016-手法はコードで実装する手法db-は索引でありコードの代替ではない)③）として別カードファイルに分離する**（`backend/app/advisor/cards/jp-market-context.md`・常時注入）。`core_prompt.md` には **persona＝反追従/職業哲学/規律のみ**を残し、市場固有知識はカードへ。これは ADR-016 の 3 層分離（CORE＝規律／カード＝参照知識）に揃えるためで、本 ADR の「ペルソナは職業アイデンティティのみ・反追従の 2 層化」の方針自体は不変。
- **関連**: [ADR-013](#adr-013-投資方針-policy-は単一チャットで育てる版管理機構は作らない)（POLICY を育てる・構造化コアは承認制）・[ADR-015](#adr-015-システムプロンプトを不変-core--可変-policyに分離する専門性は-coretool手法カードに宿す)（CORE/POLICY 2 層）・[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け)（数値は Tool の事実のみ）・[ADR-001](#adr-001-単一ユーザー前提で作る)/[ADR-009](#adr-009-日米業種リードラグ戦略は-assetvane-の分析機能とする自動トレードツールに持ち込まない)（提示専用）・[ADR-042](#adr-042-出力面ごとの-channelprofile-で-nightlychat-等の差分を一元化するcore-は横断不変)（ペルソナは全プロファイル横断で不変）・[ADR-043](#adr-043-ai-advisor-の品質回帰を-eval-スイートで検証するpytest自前-llm-as-judge別レーン)（反追従が効くかを eval で検証）。

## ADR-042: 出力面ごとの ChannelProfile で nightly/chat 等の差分を一元化する（CORE は横断不変）

- **状況**: AI Advisor は同じ「脳」（CORE＋POLICY＋Tool）を複数の出力面で使う（[ADR-011](#adr-011-ai-advisor-を-2-軸夜の分析ai相談チャットaiで実装する製品の核心) の 2 軸＋将来 Discord 双方向化＝[dexter-research.md §3.5](dexter-research.md)）。だが現状、軸1（夜の分析AI）と軸2（チャット）の差は **`backend/app/advisor/nightly.py` の `_NIGHTLY_INSTRUCTION`（ベタ書き文字列）＋ `build_messages()` に渡す `screen_context=None` 分岐**に**散在**しており、面が増えるたび分岐が増える。dexter は `ChannelProfile` registry（`src/agent/channels.ts`）で「**同じペルソナ・面ごとに振る舞いと出力形式だけ差し替える**」を実現している（[dexter-harness.md §3](dexter-harness.md)）。
- **決定**:
  - **`ChannelProfile` を導入する**（**コード定数 registry**・env を挟まない＝[ADR-027](#adr-027-手法パラメータは-phase-1-はコード定数将来-method_settingsai-は助言自動改変しない) 流に「構造データはコードと共に育てる」。dexter `channels.ts` 同型）。1 プロファイルが **behavior（行動規範）・responseFormat（出力形式）・その面固有の instruction（例: nightly の `submit_journal` 強制）・`screen_context` の有無** を保持し、「**出力面ごとの差分の単一の真実**」にする。
  - **`_NIGHTLY_INSTRUCTION` のベタ書きと `screen_context=None` 分岐を廃し、プロファイルに吸収する**。`build_messages()`（`prompt_builder.py`）はプロファイルを受け取って組み立てる。
  - **CORE／ペルソナ（[ADR-041](#adr-041-ai-advisor-の-core-に反追従規律とペルソナ層を明示するリスク選好は-policy-のまま職業アイデンティティのみ固定)）は全プロファイル横断で不変**。プロファイルが差し替えるのは振る舞い・出力形式・面固有指示のみ＝[ADR-011](#adr-011-ai-advisor-を-2-軸夜の分析ai相談チャットaiで実装する製品の核心)「1 つの脳・複数の起動口」のプロンプト層版。
  - **provider 選択はプロファイルに統合しない**。`settings.provider_for(source)`（[ADR-032](#adr-032-codex-接続は-mcpcodex-app-serverapicodex-を面別切替自動フォールバックなし) で集約済み）を参照のまま使い、二重管理を避ける（プロファイル＝プロンプトの面適応／provider_for＝LLM バックエンドの面適応、と関心を分ける）。
  - **初期は nightly / chat の 2 プロファイルを定義**。将来 **discord（双方向チャット）枠を予約**する。**Phase 6 の Discord digest は advisor の LLM 呼び出しではない別経路（`notify_digest`）なので本プロファイルの対象外**（[phase6-spec](phase-specs/phase6-spec.md)）。
- **理由**: 散在した面差分を 1 か所（registry）に集め、将来の面追加を 1 エントリで済ませる。CORE 不変・プロファイルは振る舞い/形式のみ、という分離で [ADR-011](#adr-011-ai-advisor-を-2-軸夜の分析ai相談チャットaiで実装する製品の核心) の「1 つの脳」をプロンプト層に素直に落とす。env を挟まないのは [ADR-027](#adr-027-手法パラメータは-phase-1-はコード定数将来-method_settingsai-は助言自動改変しない) の構造データ方針に揃えるため。
- **代替案**:
  - **出力形式だけプロファイル化**（instruction/screen は現状の分岐に残す）→ 面差分が 2 か所に分散したままで一元化の目的を果たさないため不採用。
  - **provider 選択もプロファイルに統合**→ [ADR-032](#adr-032-codex-接続は-mcpcodex-app-serverapicodex-を面別切替自動フォールバックなし) の `provider_for` 集約と重複し、設定の真実が二重化するため却下。
  - **プロファイルを md ファイル/DB 化**→ 構造データはコードで持つ（[ADR-027](#adr-027-手法パラメータは-phase-1-はコード定数将来-method_settingsai-は助言自動改変しない) 流）方針に反し、env/ファイル同期の手間が増えるだけで益が無いため不採用。
- **詳細**: 実装は別タスク。本 ADR は `ChannelProfile` の導入方針（registry・保持項目・`_NIGHTLY_INSTRUCTION`/screen 分岐の吸収・provider は非統合）のみ宣言する。`backend/app/advisor/{prompt_builder.py,nightly.py,router.py}`。[dexter-harness.md §3/§4.4](dexter-harness.md)。
- **関連**: [ADR-011](#adr-011-ai-advisor-を-2-軸夜の分析ai相談チャットaiで実装する製品の核心)（1 つの脳・複数の起動口）・[ADR-041](#adr-041-ai-advisor-の-core-に反追従規律とペルソナ層を明示するリスク選好は-policy-のまま職業アイデンティティのみ固定)（CORE/ペルソナは横断不変）・[ADR-032](#adr-032-codex-接続は-mcpcodex-app-serverapicodex-を面別切替自動フォールバックなし)（provider は provider_for で別管理）・[ADR-025](#adr-025-画面コンテキスト注入は軽量ヒントのみ数値は渡さない)（軸1 は画面コンテキスト無し）・[ADR-027](#adr-027-手法パラメータは-phase-1-はコード定数将来-method_settingsai-は助言自動改変しない)（構造データはコード定数）。

## ADR-043: AI Advisor の品質回帰を eval スイートで検証する（pytest＋自前 LLM-as-judge・別レーン）

- **状況**: 現状の Advisor テスト（`backend/tests/test_advisor_*.py` / `test_nightly.py`）は**決定論的な機構検証のみ**（Phase ゲート・tool_runs の形・policy 更新・縮退/失敗分岐）で、実 LLM は全モック。**「そもそも Advisor が正しい提案・正しい Tool 呼び出しをしているか」を継続検証する手段が無い**。[ADR-018](#adr-018-無人運用の障害時方針失敗を黙って放置しない)（縮退検知）・[ADR-030](#adr-030-proposed_policy_change-は単一-field-to-に構造強制するfield-は-policy-列の-enum)（構造強制）はランタイムの防御に留まり、「縮退ではないが提案がズレている」「Tool を呼ぶべき場面で生データ解釈に走った（[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け) 違反）」は拾えない。dexter は eval でフルエージェントを実走させ LLM-as-judge で採点している（[dexter-research.md §3.6](dexter-research.md)）。
- **決定**:
  - **pytest＋自前 LLM-as-judge で eval スイートを作る**（LangSmith 等の外部トレース SaaS は使わない＝[ADR-001](#adr-001-単一ユーザー前提で作る) の単一ユーザー・外部非公開に対し過剰）。
  - **通常テストから分離する**。実 LLM を叩く（ネットに出る）ため、`@pytest.mark.eval`（名称は実装時確定）で**通常の pytest（ネットに出ない＝testing-strategy）から除外**し、**手動／定期で実行**する。**CI の必須 gate にはしない**（実 LLM は非決定的・ネット必要で flaky なため）。結果は人が観測する（ログ／md）。
  - **採点は 2 層**にする（dexter は最終回答のみ採点だが AssetVane は機構＋質に広げる）:
    - **(a) 決定論アサーション**: 期待した Tool が呼ばれたか・observations が非空か・`proposed_policy_change` が単一 `{field,to}` か（[ADR-030](#adr-030-proposed_policy_change-は単一-field-to-に構造強制するfield-は-policy-列の-enum)）・`tool_runs` に結果値が載っていないか（[ADR-025](#adr-025-画面コンテキスト注入は軽量ヒントのみ数値は渡さない)）・**Tool の戻り値に無い数字を出していないか（[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け) の捏造監査）**。
    - **(b) LLM-as-judge**: 提案の妥当性・**反追従が効いているか（[ADR-041](#adr-041-ai-advisor-の-core-に反追従規律とペルソナ層を明示するリスク選好は-policy-のまま職業アイデンティティのみ固定) 連携）**。judge は本番同等の強モデル（[ADR-012](#adr-012-llm-はアダプタで抽象化openrouter-既定ローカルへ差替可)）。
  - **ゴールデンセット**は**一時 SQLite**（testing-strategy の `temp_db` 流）で市況・ポートフォリオの固定シナリオを**小さく数ケース**組み、`backend/tests/eval/` 配下に置く。**反追従ケース（ユーザーの誤った前提に同意せず事実で返すか）を含める**。
- **理由**: [ADR-018](#adr-018-無人運用の障害時方針失敗を黙って放置しない)/[ADR-030](#adr-030-proposed_policy_change-は単一-field-to-に構造強制するfield-は-policy-列の-enum) が拾えない「質のズレ」「[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け) 違反の生データ解釈」を回帰検出できる手段が、現状ゼロという最大の盲点を埋める。機構（決定論で測れる部分）＋質（judge）の 2 層にすることで、原因の切り分け（機構の回帰か・質の劣化か）ができる。外部 SaaS を避け pytest＋一時 SQLite に乗せるのは testing-strategy／[ADR-001](#adr-001-単一ユーザー前提で作る) の規律に揃えるため。
- **代替案**:
  - **決定論アサーションのみ**（judge なし）→ 「提案が妥当か・反追従が効くか」を測れず [ADR-041](#adr-041-ai-advisor-の-core-に反追従規律とペルソナ層を明示するリスク選好は-policy-のまま職業アイデンティティのみ固定) の効果検証ができないため不採用。
  - **LLM-judge 主体**（dexter 流に最終回答を丸ごと採点）→ 非決定的で、機構の回帰（期待 Tool が呼ばれたか等）まで judge のぶれに依存し原因切り分けが難しいため不採用。
  - **LangSmith 等の外部 eval 基盤**→ 単一ユーザー・外部非公開（[ADR-001](#adr-001-単一ユーザー前提で作る)）には過剰で不採用。
  - **CI の必須 gate にする**→ 実 LLM は非決定的・ネット必要で flaky になり、testing-strategy の「ネットに出ない CI」と衝突するため不採用（別レーンで手動/定期）。
- **詳細**: 実装は別タスク。本 ADR は eval の方式（pytest＋自前 judge）・分離（マーカー・非 gate）・採点 2 層・ゴールデンセットの置き場の方針のみ宣言する。testing-strategy スキルへの「eval レーン」追記は実装タスクで `project-skill-authoring` 承認を得て行う。`backend/tests/eval/`・[dexter-research.md §3.6](dexter-research.md)。
- **関連**: [ADR-018](#adr-018-無人運用の障害時方針失敗を黙って放置しない)（縮退検知＝ランタイム防御）・[ADR-030](#adr-030-proposed_policy_change-は単一-field-to-に構造強制するfield-は-policy-列の-enum)（構造強制＝予防）・[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け)（数値捏造監査）・[ADR-041](#adr-041-ai-advisor-の-core-に反追従規律とペルソナ層を明示するリスク選好は-policy-のまま職業アイデンティティのみ固定)（反追従の効果検証）・[ADR-012](#adr-012-llm-はアダプタで抽象化openrouter-既定ローカルへ差替可)（judge は強モデル）・[ADR-001](#adr-001-単一ユーザー前提で作る)（外部 SaaS 不要）。

---

> ADR-044〜047 は、ニュース（一般・銘柄・セクター・ユーザー入力）を統合コーパス化して RAG で活かす相談から確定した 4 件。スコープは設計判断（本 ADR）まで＝実装は別タスク。grill-me（`rag-grill-me-cheerful-sun`）で設計を確定した。

## ADR-044: ニュースを統合コーパスと階層タグに集約し get_news_context で3層を必ず揃える

- **状況/問題**: ニュースは現在 2 系統に分裂している＝① 銘柄ニュース（[ADR-020](#adr-020-個別銘柄ドシエ定性ファンダ調査-1銘柄1レポートを更新し続ける)・`fetch_news`→`dossier_sources`/`stock_dossiers`）と ② 一般ニュース（[ADR-034](#adr-034-一般ニュースダイジェスト銘柄に紐づかないニュースを別系統で持つ実装済み)・`fetch_general_news`→`general_news`・3 カテゴリ×5 件・lookback 2 日）。どちらも本文を捨て「要約＋URL」だけ持つ（ADR-020 の流儀）。利用者の出発点の要求は「AI Advisor に『この銘柄どう？』と相談したとき、AI がニュースを使いやすくしたい」で、1 銘柄の分析には **3 階層の文脈**＝(i) その銘柄自身／(ii) その銘柄のセクター／(iii) マーケット全体（「市況が冷え込んでいるから…」）が要る。だが現状、(ii) セクターニュースは**存在せず**、(i)(iii) も別 Tool（`fetch_news`/`get_general_news`）に割れていて、AI が 3 階層を自力で揃える保証が無い。
- **検討して却下した案**:
  - **① 完全フラットな単一 "news" コーパス＋意味検索（RAG）だけにする** → **却下**。純粋な意味検索で「6758 どう？」を引くと、銘柄自身の記事は取れてもマーケット/マクロのニュース（「金利上昇で株安」）は**銘柄語と意味的に遠く埋もれる**＝利用者が一番欲しい (iii) の文脈を取りこぼす。階層は検索で消してはいけない。
  - **② 3 系統テーブルを別々のまま維持する** → 却下。ユーザー入力（YouTube 等＝[ADR-046](#adr-046-ユーザー入力ニュースはテキスト貼付から要約して統合コーパスに入れる)）や横断検索（[ADR-045](#adr-045-ニュース意味検索は段階導入する初手は-embedding-と-sqlite-vec最終は-fts5-ハイブリッド)）を足すたび系統が増え、統合が利かない。住所が「対象」ではなく「保存先」で割れる。
- **決定**:
  - **保存は統合コーパス 1 本に集約**（[ADR-034](#adr-034-一般ニュースダイジェスト銘柄に紐づかないニュースを別系統で持つ実装済み)の `general_news` を発展置換）。記事ごとに **`level`（`stock`/`sector`/`market`/`user`）・`sector17_code`・`code`・`source`** のタグを持たせる。本文は持たず**要約＋URL のみ**（ADR-020 堅持）。`url` UNIQUE＋冪等 UPSERT も踏襲。
  - **セクターニュース取り込みを新設**。`services/lead_lag.py` の `JP_SECTOR_LABELS`（TOPIX-17 業種和名）を起点に「業種名→Google News 検索クエリ」のマップを定数モジュール（[ADR-034](#adr-034-一般ニュースダイジェスト銘柄に紐づかないニュースを別系統で持つ実装済み)の `general_news_config.py` 流）に置き、既存のキーワード検索パイプライン（`_fetch_rss_items`/`_process_item` の 3 段フォールバック要約）を再利用する。銘柄の `sector17_code`（[ADR-039](#adr-039-phase-7-を-a-sector-lead-lag-先行b-米株拡張に分割し-a-の業種-etf-は-indexadapter-に-yahoo-ソースを足して流用する) で確立・`stocks` 列）でセクターを解決する。
  - **`get_news_context(code)` Tool を新設**（`min_phase=4`）。銘柄の `sector17_code` を解決し、**タグフィルタで (i) 銘柄／(ii) セクター／(iii) マーケットの 3 層を必ず揃えて**構造的に返す。これにより (iii) のマクロ層が意味検索で埋もれる問題を回避する。AI は受け取った事実を解釈するだけ（[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け)）。
  - **取り出しは 2 系統**にする＝本 ADR の **構造的 `get_news_context`**（3 層保証）と、[ADR-045](#adr-045-ニュース意味検索は段階導入する初手は-embedding-と-sqlite-vec最終は-fts5-ハイブリッド)の **意味的 `search_news`**（横断検索）。`fetch_news`/`get_general_news` は当面据え置き、移行は実装タスクで判断する。
- **後続（本 ADR を土台に確定）**: タグ集合に定性 `polarity`（[ADR-049](#adr-049-ニュース-rag-の活用を線引きするai-は事実を解釈するだけで数値スコアは作らない)）と `theme`（[ADR-050](#adr-050-銘柄とニュースにテーマタグを持たせ語彙揺れをプロンプト照合と-embedding-近接で抑える)）を加える／`get_news_context` の活用線引き（[ADR-049](#adr-049-ニュース-rag-の活用を線引きするai-は事実を解釈するだけで数値スコアは作らない)）・能動配信（[ADR-051](#adr-051-ニュースとシグナルと保有を結ぶ能動配信を-notify_digest-に拡張する)）・売買アイデア（[ADR-052](#adr-052-ニュース起点の売買アイデアは-proposals-の-buysell-に承認制で起票する)）。
- **理由**: 「保存は統合・階層はタグで保持」が、フラット RAG（マクロ層が埋もれる）と 3 系統分裂（統合が利かない）の両方の欠点を回避する。3 層を**構造で**揃えることで、利用者の中核要求（市況文脈を必ず添える）を Tool の設計で担保できる。[ADR-020](#adr-020-個別銘柄ドシエ定性ファンダ調査-1銘柄1レポートを更新し続ける)/[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け) の不変条件（要約のみ・AI は計算しない）を一切崩さない。
- **段階**: **実装済み（2026-06-07）**。pytest green。実装＝新テーブル `news`（`schema.py`・migration `0013_news_corpus`〔`down_revision=0012`〕で旧 `general_news`＋`dossier_sources` を完全置換）／repo の `upsert_news`・`news_exists`・`list_news`（旧 `upsert_general_news`/`list_general_news`/`upsert_dossier_source`/`dossier_source_exists`/`list_dossier_sources` を統合置換）／`adapters/news.py` のセクターニュース取得＋要約前 dedup／`adapters/general_news_config.py` の `SECTOR_NEWS_QUERIES`／夜間ジョブ `batch/jobs/fetch_sector_news.py`（`fetch_general_news` の直後・`run_advisor` の前）／`services/news.py` の `build_news_context`／`advisor/tools` の `get_news_context`（`min_phase=4`・読み取り専用）。既存消費先（`GET /general-news`／`GET /dossiers/{code}`／`get_general_news`/`get_dossier` Tool）は内部を `list_news` に張り替え・レスポンス形は不変。**後続として [ADR-046](#adr-046-ユーザー入力ニュースはテキスト貼付から要約して統合コーパスに入れる)（ユーザー入力）／[ADR-047](#adr-047-統合ニュースページ-news-を新設する一覧と検索と貼付フォーム)（`/news` 画面）は実装済み（2026-06-08）**。[ADR-045](#adr-045-ニュース意味検索は段階導入する初手は-embedding-と-sqlite-vec最終は-fts5-ハイブリッド)（意味検索）／[ADR-049](#adr-049-ニュース-rag-の活用を線引きするai-は事実を解釈するだけで数値スコアは作らない)〜052 と `polarity`/`theme` 列も実装済み（`theme`＝[ADR-050](#adr-050-銘柄とニュースにテーマタグを持たせ語彙揺れをプロンプト照合と-embedding-近接で抑える)・`0018`／`polarity`＝[ADR-049](#adr-049-ニュース-rag-の活用を線引きするai-は事実を解釈するだけで数値スコアは作らない)/[ADR-051](#adr-051-ニュースとシグナルと保有を結ぶ能動配信を-notify_digest-に拡張する)・`0020`）。schema は [data-model.md](data-model.md)、Tool は [advisor.md](advisor.md) に同期済み。
- **関連**: [ADR-034](#adr-034-一般ニュースダイジェスト銘柄に紐づかないニュースを別系統で持つ実装済み)（前身・発展置換）・[ADR-020](#adr-020-個別銘柄ドシエ定性ファンダ調査-1銘柄1レポートを更新し続ける)（要約のみ）・[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け)/[ADR-016](#adr-016-手法はコードで実装する手法db-は索引でありコードの代替ではない)（AI は計算しない・手法はコード）・[ADR-010](#adr-010-データソースはアダプタ越しにする)（アダプタ越し）・[ADR-009](#adr-009-自動売買はしない提示に徹する)（提示専用）・[ADR-039](#adr-039-phase-7-を-a-sector-lead-lag-先行b-米株拡張に分割し-a-の業種-etf-は-indexadapter-に-yahoo-ソースを足して流用する)（sector17/lead_lag）・[ADR-045](#adr-045-ニュース意味検索は段階導入する初手は-embedding-と-sqlite-vec最終は-fts5-ハイブリッド)（意味検索）・[ADR-046](#adr-046-ユーザー入力ニュースはテキスト貼付から要約して統合コーパスに入れる)（ユーザー入力）・[ADR-047](#adr-047-統合ニュースページ-news-を新設する一覧と検索と貼付フォーム)（一覧画面）。
- ※ `sector17_code` のコード体系は S17（"1".."17"）に統一＝[ADR-053](#adr-053-sector17-の二体系分類-s17--銘柄-etf-ティッカーの境界を固定し業種コード参照知識を-appreference-に集約する) で確定（ADR-044 実装時にセクター層を ETF ティッカー "1617".."1633" でタグ付けしていた取り違えを修正）。

## ADR-045: ニュース意味検索は段階導入する（初手は embedding と sqlite-vec・最終は FTS5 ハイブリッド）

- **状況/問題**: [ADR-044](#adr-044-ニュースを統合コーパスと階層タグに集約し-get_news_context-で3層を必ず揃える)で統合コーパスができても、構造的 `get_news_context` は直近窓（lookback）の 3 層を揃えるだけで、「利上げ観測に関する話」「先週のあの決算ニュース」のような**曖昧・過去横断のクエリ**には応えられない。利用者は「過去のニュースも遡って意味で引きたい」と明示した。[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け) のタイトルが既に「RAG は後付け」と予約しており、`data-model`/phase3-spec も「将来 sqlite-vec で意味検索」と段階計画を持つが、ベクトル基盤は未実装。
- **検討して却下した案**:
  - **① FTS5 キーワード全文索引だけ** → 却下（単体では）。新依存ゼロで過去のキーワード検索は強いが、「意味で引く」（語が一致しなくても近い記事）には届かず利用者要求を満たさない。ただし**併用は最終形に取り込む**（下記 C）。
  - **② 外部ベクトル DB（chromadb/pgvector 等）** → 却下。別プロセスが DB を持つと [ADR-005](#adr-005-db-に触れるのは-fastapi-のみnext-は-rest-経由)（DB に触れる OS プロセスは FastAPI のみ）・[ADR-002](#adr-002-データベースは-sqlitewal-モード)（SQLite/WAL）に反する。単一ユーザー・ラズパイ母艦に過剰。
  - **③ 最初からハイブリッド（C）を作る** → 却下（初手としては）。価値はあるが FTS5＋embedding＋融合ランキングを一度に組むのは複雑。段階を踏む。
- **決定**:
  - **`search_news(query)` Tool を新設**（意味検索＋`level`/`sector17_code`/`code`/期間タグフィルタ）。過去横断（lookback 窓を越える）を可能にする。
  - **段階導入を明文化する**。**初手 = A**＝要約を embedding し **`sqlite-vec` でベクトル検索**（プロセス内拡張＝[ADR-005](#adr-005-db-に触れるのは-fastapi-のみnext-は-rest-経由)/[ADR-002](#adr-002-データベースは-sqlitewal-モード)を満たす）。embedding 生成はクラウド API（[ADR-012](#adr-012-llm-はアダプタで抽象化openrouter-既定ローカルへ差替可) の OpenAI 互換枠・推論のみで [ADR-006](#adr-006-機械学習の学習は別-pcラズパイは推論のみ)とも矛盾しない軽さ）。**最終 = C**＝FTS5 キーワード索引を足して**ハイブリッド**（安いキーワードで広く拾い embedding で意味順に並べ替え）。利用者合意＝最終 C・初手 A 可。
  - **embedding するのは要約のみ**（本文は持たない＝[ADR-020](#adr-020-個別銘柄ドシエ定性ファンダ調査-1銘柄1レポートを更新し続ける)/[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け)）。コーパスは要約のまま貯まり続けるので、後から embedding 列／FTS5 索引を張れば過去分も遡及索引化できる（A→C も追加列で移行可能）。
- **理由**: 利用者要求（意味検索＋過去横断）に FTS5 単体では届かず、外部ベクトル DB は不変条件に反する。`sqlite-vec` はプロセス内で [ADR-005](#adr-005-db-に触れるのは-fastapi-のみnext-は-rest-経由)を守れる唯一筋。A→C の段階化は、まず意味検索の価値を最小インフラで確かめ、効果を見てハイブリッドへ広げるため（[ADR-027](#adr-027-手法パラメータは-phase-1-はコード定数将来-method_settingsai-は助言自動改変しない)流の「まず小さく・将来育てる」）。
- **段階**: **段階A 実装済み（2026-06-09）／vec0 昇格・段階C（FTS5）は将来**。pytest green。
  - **実体（段階A の確定）**: 統合コーパス `news` に **`embedding`（float32 LE の BLOB・null 可）／`embed_model`／`embedded_at` の 3 列を追加**（migration `0016_news_embedding`・`down_revision=0015`）。検索は **`sqlite-vec` の `vec_distance_cosine` で BLOB 列を直接スキャン**する＝**vec0 仮想テーブルは使わず次元非依存**（モデル差替で次元が変わっても格納形式は不変）。`embed_model` 列でモデル不一致行を検出し**再埋め込み**対象にする（未埋め込み行は `embedding IS NULL`）。**embedding するのは `summary` のみ**（本文は持たない＝[ADR-020](#adr-020-個別銘柄ドシエ定性ファンダ調査-1銘柄1レポートを更新し続ける)堅持）。
  - **プロバイダは OpenAI 互換 1 本のみ**＝chat と同型（[ADR-012](#adr-012-llm-はアダプタで抽象化openrouter-既定ローカルへ差替可)）。`embedding_base_url`/`embedding_api_key`/`embedding_model` の差替で openai 直・localllm を吸収する（`adapters/embedding.py`）。**Anthropic/Voyage 専用ブランチは作らない**。3 つのいずれかが未設定なら **静かに機能オフ**＝`search` は items 空＋reason・`embed_news` は skip（[ADR-006](#adr-006-機械学習の学習は別-pcラズパイは推論のみ)/[ADR-018](#adr-018-無人運用の障害時方針失敗を黙って放置しない)）。
  - **生成は 2 経路**＝① 夜間ジョブ **`embed_news` 一本**（要約が出揃った後・`embedding` が null またはモデル不一致の行をまとめて埋める＝バックフィル＋新規＋再埋め込み）／② 貼付 `ingest_user_news` の **best-effort 即時埋め込み**（機能オン時のみ・失敗は握って夜ジョブが拾う）。
  - **露出は 3 面**＝AI Tool **`search_news`**（`min_phase=4`・読み取り専用）／REST **`GET /news/search`**／frontend **`/news` の検索ボックス**。いずれも `level`/`code`/`sector17_code`/`since`/`until` の絞り込み付きで過去横断する。
  - **【明示 TODO・ドキュメント管理＝vec0 へ昇格】**: コーパスが育ったら BLOB 全件スキャンから **vec0 仮想テーブル索引へ昇格**する（**`embedding` 列はそのまま活きる**＝再埋め込み不要）。**発火条件の叩き台＝コーパス概ね 5 万行 or 検索レイテンシ実測 >200ms**（どちらか）。
  - **段階C（FTS5 キーワード索引ハイブリッド）は従来どおり将来**＝安いキーワードで広く拾い embedding で意味順に並べ替える（FTS5 索引も後付け列／仮想テーブルで遡及できる）。
  - schema は [data-model.md](data-model.md)、REST は [api.md](api.md)、Tool は [advisor.md](advisor.md) に同期済み。
- **関連**: [ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け)（RAG は後付け＝本 ADR で具体化）・[ADR-044](#adr-044-ニュースを統合コーパスと階層タグに集約し-get_news_context-で3層を必ず揃える)（統合コーパス前提）・[ADR-020](#adr-020-個別銘柄ドシエ定性ファンダ調査-1銘柄1レポートを更新し続ける)（要約のみ embedding）・[ADR-012](#adr-012-llm-はアダプタで抽象化openrouter-既定ローカルへ差替可)（embedding もクラウド）・[ADR-006](#adr-006-機械学習の学習は別-pcラズパイは推論のみ)（推論のみ・学習は別 PC）・[ADR-002](#adr-002-データベースは-sqlitewal-モード)/[ADR-005](#adr-005-db-に触れるのは-fastapi-のみnext-は-rest-経由)（sqlite-vec はプロセス内）・[ADR-025](#adr-025-画面コンテキスト注入は軽量ヒントのみ数値は渡さない)（事実は Tool で取り直す）。

## ADR-046: ユーザー入力ニュースはテキスト貼付から要約して統合コーパスに入れる

- **状況/問題**: 利用者は「自分や他媒体（YouTube のトランスクリプト・要約など）からのニュースも入力したい」と要求した。現状ニュースは外部 RSS 取得（`fetch_news`/`fetch_general_news`）のみで、利用者が手元の一次資料を投入する経路が無い。
- **検討して却下した案**:
  - **(a) YouTube URL を貼ると自動でトランスクリプト取得→要約** → **却下**（初手としては）。`youtube-transcript-api` 等の新依存が要り、字幕無し動画で失敗し、無人取得が脆い（[ADR-020](#adr-020-個別銘柄ドシエ定性ファンダ調査-1銘柄1レポートを更新し続ける)が夜のニュース取得を httpx 一本に軽くした思想と同根の弱点）。YouTube 限定なのも狭い。**将来の砂糖衣として予約**。
  - **(c) 利用者が書いた要約を無加工で保存** → 却下。一番安いが全部手作業で、要約の粒度・形式が揃わず統合コーパスの品質が割れる。
- **決定**:
  - **(b) テキスト貼付→システムが要約→統合コーパスへ投入**。利用者が本文/トランスクリプト/記事をテキストで貼り、既存の要約パイプライン（`adapters/news.py` の `_summarize_article`／`generate_once(source="dossier")`）を再利用して 2〜3 行に要約する。**YouTube に限らず何でも**（記事・PDF コピペ等）入る万能口。
  - **保存は [ADR-044](#adr-044-ニュースを統合コーパスと階層タグに集約し-get_news_context-で3層を必ず揃える)の統合コーパスに 1 ドキュメントとして**＝`source`=`user`（必要なら `youtube` 等に細分）・`level` は内容に応じて（市況なら `market`、銘柄紐付けがあれば `code`＋`stock`）タグ付け。**本文は保存せず要約＋利用者が付した元参照（URL 等・任意）のみ**（ADR-020 堅持）。embedding・検索（[ADR-045](#adr-045-ニュース意味検索は段階導入する初手は-embedding-と-sqlite-vec最終は-fts5-ハイブリッド)）の対象にも自然に乗る。
  - **投入口は [ADR-047](#adr-047-統合ニュースページ-news-を新設する一覧と検索と貼付フォーム)の `/news` ページの貼付フォーム**（＋REST エンドポイント）。書き込みは FastAPI 経由のみ（[ADR-005](#adr-005-db-に触れるのは-fastapi-のみnext-は-rest-経由)）。
- **理由**: (b) は新しい脆い取得器を持たずに「YouTube 以外もやりたい」を満たす最も汎用な形で、既存要約を再利用でき、統合コーパスの不変条件（要約のみ・タグ付き）にそのまま乗る。(a) は価値はあるが依存と運用脆さを後回しにできる。
- **段階**: **実装済み（2026-06-08）**。pytest green。実装＝`adapters/news.py` の要約関数を `_summarize_article`→public `summarize_article` にリネームし再利用／`services/news.py` の `ingest_user_news`＋`_resolve_user_tags`＋`_user_news_url`／`db/repo.py` の `get_news_by_url`・`delete_user_news`／新ルータ `routers/news.py`（`POST /news`＝要約→UPSERT・`DELETE /news/{id}`）／`main.py` 登録。**タグ v1 はユーザー明示**＝銘柄コードありで `level='stock'`＋`code`、無しで `level='market'`、`source` は常に `'user'`（`level='user'` 値は schema 上許容だが本実装は未使用）。**URL 未入力時は合成キー `user://`＋`sha256(text)` 先頭 16 桁**を `url`（NOT NULL UNIQUE）に詰め、冪等は `upsert_news` の `on_conflict_do_nothing` に委ねる。**要約失敗時は 502 を返し保存しない**（同期・対面で再試行）。**削除は `source='user'` のみ可**（自動取得分は不可＝404）。**code 無しユーザー投入は `level='market'`＋`category="ユーザー投入"`** を付与し `GET /general-news` にも出るようにする。**migration なし**（既存 `news` 列で表現）。**スコープは ADR-046／[ADR-047](#adr-047-統合ニュースページ-news-を新設する一覧と検索と貼付フォーム) のみ**＝[ADR-045](#adr-045-ニュース意味検索は段階導入する初手は-embedding-と-sqlite-vec最終は-fts5-ハイブリッド)（意味検索）は未実装のまま。[api.md](api.md)/[data-model.md](data-model.md)/[screens.md](screens.md) に同期済み。
- **関連**: [ADR-044](#adr-044-ニュースを統合コーパスと階層タグに集約し-get_news_context-で3層を必ず揃える)（同コーパスへ・タグ）・[ADR-020](#adr-020-個別銘柄ドシエ定性ファンダ調査-1銘柄1レポートを更新し続ける)（本文捨て要約のみ）・[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け)（AI は要約だけ・数値計算なし）・[ADR-045](#adr-045-ニュース意味検索は段階導入する初手は-embedding-と-sqlite-vec最終は-fts5-ハイブリッド)（投入物も意味検索対象）・[ADR-047](#adr-047-統合ニュースページ-news-を新設する一覧と検索と貼付フォーム)（投入 UI）・[ADR-005](#adr-005-db-に触れるのは-fastapi-のみnext-は-rest-経由)（書き込みは FastAPI）。

## ADR-047: 統合ニュースページ news を新設する（一覧と検索と貼付フォーム）

- **状況/問題**: 一般ニュースの**一覧画面が無い**（Dashboard の `GeneralNewsWidget` でちら見できるだけ＝[ADR-034](#adr-034-一般ニュースダイジェスト銘柄に紐づかないニュースを別系統で持つ実装済み)「専用ページは作らない」）。だが統合コーパス（[ADR-044](#adr-044-ニュースを統合コーパスと階層タグに集約し-get_news_context-で3層を必ず揃える)）・横断検索（[ADR-045](#adr-045-ニュース意味検索は段階導入する初手は-embedding-と-sqlite-vec最終は-fts5-ハイブリッド)）・利用者投入（[ADR-046](#adr-046-ユーザー入力ニュースはテキスト貼付から要約して統合コーパスに入れる)）が乗ると、これらの「顔」となる画面が要る。利用者も一覧画面を望んだ。
- **検討して却下した案**:
  - **一般ニュースだけの一覧ページ** → 却下。セクター/銘柄/ユーザー入力が別画面のままで、統合コーパスと画面構造が一致しない。検索・投入の置き場も無い。
- **決定**:
  - **統合「ニュース」ページ `/news` を新設**＝① 一覧（`level`/セクター/`source`/期間でフィルタ）＋② 検索ボックス（[ADR-045](#adr-045-ニュース意味検索は段階導入する初手は-embedding-と-sqlite-vec最終は-fts5-ハイブリッド)の `search_news`＝意味＋将来キーワード）＋③ 「ニュースを貼る」入力フォーム（[ADR-046](#adr-046-ユーザー入力ニュースはテキスト貼付から要約して統合コーパスに入れる)）。一覧・検索・投入を 1 画面に集約し、統合コーパスの単一の顔にする。
  - **Dashboard の `GeneralNewsWidget` は "ちら見" 用に残す**（[ADR-034](#adr-034-一般ニュースダイジェスト銘柄に紐づかないニュースを別系統で持つ実装済み)実装を温存・全面置換しない）。常駐 Advisor チャット（[ADR-024](#adr-024-ai-advisor-チャットを全ページ常駐にするフローティング)）はそのまま。
  - Next.js は UI 専用でデータは REST 経由（[ADR-005](#adr-005-db-に触れるのは-fastapi-のみnext-は-rest-経由)）。画面の数値はチャットに載せない（[ADR-025](#adr-025-画面コンテキスト注入は軽量ヒントのみ数値は渡さない)）。
- **理由**: 統合コーパスには統合した顔が要る。一覧・検索・投入を 1 枚に集約すれば、利用者の「一覧が欲しい」「検索したい」「貼りたい」を 1 動線で満たし、コーパス設計と画面が一致する。widget 温存で既存の "ちら見" 体験は壊さない。
- **段階**: **実装済み（2026-06-08）**。実装＝frontend の `/news` ページ（一覧＋`level` 単一タブ＋期間フィルタ＋「ニュースを貼る」フォーム＋`source='user'` 行の削除）／`lib/api.ts` の `getNews`/`ingestNews`/`deleteNews`（`GET/POST/DELETE /news`）／ナビに News を追加。backend の `GET /news`（`level`/`since`/`limit` で新着順一覧）は [ADR-046](#adr-046-ユーザー入力ニュースはテキスト貼付から要約して統合コーパスに入れる) 実装と同時に新設。**検索ボックスは出さない**（[ADR-045](#adr-045-ニュース意味検索は段階導入する初手は-embedding-と-sqlite-vec最終は-fts5-ハイブリッド) 送り・未実装）。**Dashboard の `GeneralNewsWidget` は温存**（別物）。**スコープは [ADR-046](#adr-046-ユーザー入力ニュースはテキスト貼付から要約して統合コーパスに入れる)／ADR-047 のみ**。[screens.md](screens.md)/[api.md](api.md) に同期済み。
- **関連**: [ADR-034](#adr-034-一般ニュースダイジェスト銘柄に紐づかないニュースを別系統で持つ実装済み)（widget は残す・専用ページ非作成を覆す）・[ADR-044](#adr-044-ニュースを統合コーパスと階層タグに集約し-get_news_context-で3層を必ず揃える)/[ADR-045](#adr-045-ニュース意味検索は段階導入する初手は-embedding-と-sqlite-vec最終は-fts5-ハイブリッド)/[ADR-046](#adr-046-ユーザー入力ニュースはテキスト貼付から要約して統合コーパスに入れる)（コーパス・検索・投入）・[ADR-005](#adr-005-db-に触れるのは-fastapi-のみnext-は-rest-経由)（UI 専用・REST 経由）・[ADR-024](#adr-024-ai-advisor-チャットを全ページ常駐にするフローティング)（チャット常駐）・[ADR-025](#adr-025-画面コンテキスト注入は軽量ヒントのみ数値は渡さない)（画面数値を載せない）。

---

## ADR-048: 銘柄バリュエーション判断基準（ROE/PER/PBR）を Tool（事実）＋参照知識カードで持たせる

- **状況**: 利用者が「ROE/PER/PBR の一般的な判断基準（"PBR がこれくらいなら割安" 等）を AI Advisor に持たせたい」と要望（dexter-jp〔日本株フォーク `~/Develop/dexter-jp`〕の調査が起点）。現状を確認すると、AssetVane には**バリュエーション軸が既にある**（`valuation_snapshots`＝PER/PBR/時価総額/配当利回り＋`/stocks/screen` スクリーナー・[ADR-031](#adr-031-株式スクリーナー夜間-valuation_snapshots-読み取り時ランク市場ごとに分離)）が、これは signals（momentum/volume_spike/lead_lag＝「強さの材料」・[ADR-026](#adr-026-signals-は連続スコアの材料ai-が主消費者で閾値は破壊的ゲートにしない)）とは**別軸**。だが **(a) 判断の作法を AI に与える参照知識が無い**、**(b) PER/PBR を返す Advisor Tool が無い**（`get_financials` は EPS/BPS どまり）、**(c) ROE/利益率/成長率を計算していない**——の 3 点が欠けていた。grill-me（`effervescent-salamander`）で設計を確定。
- **決定（8 点）**:
  - **(1) 判断基準の形＝事実は Tool・基準は参照知識**。Tool が PER/PBR/ROE＋業種内パーセンタイル等の「事実」を返し、判断ヒューリスティック（典型レンジ・PER 単体禁止・成長/品質クロスチェック）は参照知識カードに置く。**「PER≤15 なら割安」の数値ゲートはコードに作らない**（[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け)/[ADR-016](#adr-016-手法はコードで実装する手法db-は索引でありコードの代替ではない)/[ADR-026](#adr-026-signals-は連続スコアの材料ai-が主消費者で閾値は破壊的ゲートにしない)/[ADR-031](#adr-031-株式スクリーナー夜間-valuation_snapshots-読み取り時ランク市場ごとに分離) を維持）。Tool の戻り値に verdict（割安/割高の判定）は持たせず、解釈は LLM が行う。
  - **(2) AI Advisor は共通**（市場ごとに分けない）。市場差は (a) データ層（日本株は JP スナップショット／米株は Phase 7(B) の別スナップショット＝[ADR-039](#adr-039-phase-7-を-a-sector-lead-lag-先行b-米株拡張に分割し-a-の業種-etf-は-indexadapter-に-yahoo-ソースを足して流用する)）と (b) 参照知識カードにのみ出す（[ADR-011](#adr-011-ai-advisor-を-2-軸夜の分析ai相談チャットaiで実装する製品の核心)「1 つの脳」・[ADR-013](#adr-013-投資方針-policy-は単一チャットで育てる版管理機構は作らない) 単一 policy を侵さない。日米横断のバランスを 1 つの Advisor で見られる）。
  - **(3) 指標の範囲（今回）**＝既存 PER/PBR/時価総額/配当利回り ＋ **ROE(=EPS/BPS)・営業利益率・純利益率・売上/営業利益/純利益/EPS の YoY 成長率** ＋ 業種内パーセンタイル/順位。当期は最新 FY 行、YoY は前期 FY 行と突合（[ADR-031](#adr-031-株式スクリーナー夜間-valuation_snapshots-読み取り時ランク市場ごとに分離) の採用規律に揃える）。
  - **(4) 市場は Tool 契約に明示**。新 Tool の戻り値に `market`（今は `"JP"` 固定）・`currency`（`"JPY"`）を載せ契約を固定。**相対ランク（業種内パーセンタイル・時価総額順位）は市場内に閉じる**（¥と$を混ぜない＝[ADR-031](#adr-031-株式スクリーナー夜間-valuation_snapshots-読み取り時ランク市場ごとに分離)）。"both"（日米横断）は portfolio/資産概要レイヤ（Phase 7(B) の FX 換算）の仕事で、バリュエーション・ランク層ではやらない。
  - **(5) 参照知識は別カードファイルに分離**。`backend/app/advisor/cards/valuation.md`（判断ヒューリスティック）と `backend/app/advisor/cards/jp-market-context.md`（日本市場の文脈）。`core_prompt.md` は persona/規律のまま、カードを併読する。→ **[ADR-041](#adr-041-ai-advisor-の-core-に反追従規律とペルソナ層を明示するリスク選好は-policy-のまま職業アイデンティティのみ固定) を改訂**（JP 市場"知識"は core_prompt 内の節ではなくカードへ）。カードの置き場が `docs/methods/`（lead-lag.md 等の設計参照・runtime 非注入）ではなく backend 配下なのは、本番イメージで backend だけが配布され runtime 注入が必要なため。
  - **(6) Tool は 2 つ新設**：単票 `get_valuation(code)`（PER/PBR/ROE/利益率/配当利回り/YoY＋業種内ランク）＋ `screen_valuation(criteria)`（バリュエーション条件で候補を絞る・既存 `repo.screen_stocks` を流用）。`get_financials` は生財務のまま不変。signal ベースの既存 `screen_stocks` とは別物。**しきい値は AI がカードの作法を見て explicit に criteria へ渡す**（コードはゲートを持たない）。min_phase=2。
  - **(7) カード注入は常時注入**（`build_messages` の `method_cards` スロット・`backend/app/advisor/method_cards.py` がカードを起動時ロード・[ADR-016](#adr-016-手法はコードで実装する手法db-は索引でありコードの代替ではない) の「全列挙」段階）。
  - **(8) web の Tool カタログ表示・パラメータ編集 UI は今回スコープ外**（[ADR-027](#adr-027-手法パラメータは-phase-1-はコード定数将来-method_settingsai-は助言自動改変しない) の method_settings 将来作業・別タスク）。
- **理由**: dexter-jp は判断基準を「外部 SaaS のサーバ計算＋LLM 暗算」で持つが、これは [ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け)/[ADR-016](#adr-016-手法はコードで実装する手法db-は索引でありコードの代替ではない) と衝突する。AssetVane は「知識の中身（どの指標・どのレンジ・どの作法）」だけを拝借し、計算はテスト済み quant 純関数、判断作法は参照知識カード、という 3 層分離（[ADR-016](#adr-016-手法はコードで実装する手法db-は索引でありコードの代替ではない)）に落とす。これで「AI が ROE/PER/PBR を根拠に割安/割高を解釈・提示できる」を、ハルシネーション防止と再現性を保ったまま実現する。
- **将来 TODO（落とさない・明示管理）**:
  - **25 指標フル充足**: ROA/ROIC/自己資本比率/D-E/流動比率/EBITDA は総資産・負債を要し、現 `financials`（売上/営業利益/純利益/EPS/BPS/配当/株数）に無い。J-Quants `fins/summary` の総資産系フィールド有無を実機確認のうえ、財務取得を拡張してから後付けする（[roadmap.md](roadmap.md) にも記載）。
  - **カードローダ機構（近接の planned 項目）**: 今は全カード常時注入だが、**カードが増える前に**メタデータだけ常時露出・本文は選ばれた時にロードする on-demand 機構（progressive disclosure・dexter skill 型＝[dexter-research.md §3.3](dexter-research.md)）を用意する。「カードが増えたら」ではなく**近い時期の予定**として追跡する。
  - **米株バリュエーション**: `/us-stocks` 別スナップショット・通貨列・FX 換算・日米横断の "both"（Phase 7(B)・[ADR-039](#adr-039-phase-7-を-a-sector-lead-lag-先行b-米株拡張に分割し-a-の業種-etf-は-indexadapter-に-yahoo-ソースを足して流用する)）。
- **代替案**:
  - **ハードしきい値を quant に持つ**（「PER≤15=割安」フラグを焼く）→ [ADR-026](#adr-026-signals-は連続スコアの材料ai-が主消費者で閾値は破壊的ゲートにしない)「破壊的ゲートにしない・AI が濃淡を見る」と緊張し、再現性は上がるが硬直。不採用（しきい値は参照知識カードの"起点"に留め、絞りは AI が criteria で動かす）。
  - **`get_financials` を拡張して PER/PBR/ROE を相乗り** → 「生財務」と「派生比率＋ランク」の責務が混ざる。新 Tool に分離。
  - **Advisor を JP/US で分割** → policy が 2 つに割れ（[ADR-013](#adr-013-投資方針-policy-は単一チャットで育てる版管理機構は作らない) に反する）・日米横断バランスが見にくい。共通 Advisor＋データ層分離を採用。
  - **判断基準を外部 SaaS（dexter-jp の EDINET DB 型）に委ねる** → [ADR-008](#adr-008-j-quants-は-v2x-api-key-を使うv1-は使わない)（日本株は J-Quants）/[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け)（数値は自前 quant）と衝突。却下。
- **段階**: **実装済み（2026-06-07）**。pytest green（quant 純関数・services スナップショット・migration 0012 回帰・Tool handler の戻り値形＝market/currency 明示・verdict 不在）。
- **詳細**: `backend/app/quant/valuation.py`（roe/operating_margin/net_margin/growth_yoy 純関数）・`services/valuation.py`（`_fundamentals`）・`db/repo.py`（`get_prior_annual_financials_by_code`・`get_valuation_snapshot`・`_valuation_inner_subquery`・`screen_stocks` の range/sort 拡張）・`db/schema.py`＋`alembic/versions/0012_valuation_metrics.py`（valuation_snapshots に 7 列追加）・`advisor/tools/{schemas,handlers,registry}.py`（`get_valuation`/`screen_valuation`・min_phase=2）・`advisor/method_cards.py`＋`advisor/cards/{valuation.md,jp-market-context.md}`・`advisor/{router,nightly}.py`（カード常時注入）。数値の出典参考は dexter-jp の `src/tools/finance/screen-companies.ts`（曖昧語→数値の既定値）・`src/skills/dcf/SKILL.md`（PER/PBR/WACC レンジ）・`RULES.md.example`（バリュー投資ルール例）。
- **関連**: [ADR-031](#adr-031-株式スクリーナー夜間-valuation_snapshots-読み取り時ランク市場ごとに分離)（valuation_snapshots・市場分離）・[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け)/[ADR-016](#adr-016-手法はコードで実装する手法db-は索引でありコードの代替ではない)（計算はコード・参照知識は markdown）・[ADR-026](#adr-026-signals-は連続スコアの材料ai-が主消費者で閾値は破壊的ゲートにしない)（破壊的ゲートにしない）・[ADR-011](#adr-011-ai-advisor-を-2-軸夜の分析ai相談チャットaiで実装する製品の核心)/[ADR-013](#adr-013-投資方針-policy-は単一チャットで育てる版管理機構は作らない)（1 つの脳・単一 policy）・[ADR-041](#adr-041-ai-advisor-の-core-に反追従規律とペルソナ層を明示するリスク選好は-policy-のまま職業アイデンティティのみ固定)（改訂＝JP 文脈はカードへ）・[ADR-039](#adr-039-phase-7-を-a-sector-lead-lag-先行b-米株拡張に分割し-a-の業種-etf-は-indexadapter-に-yahoo-ソースを足して流用する)（米株 Phase 7(B)）・[ADR-027](#adr-027-手法パラメータは-phase-1-はコード定数将来-method_settingsai-は助言自動改変しない)（web 設定 UI は将来）。

---

> ADR-049〜052 は、ニュース DB を RAG 化して株分析に活かす活用案（`.tmp/20260607_ai_rag.txt`＝外部 AI の提案 7 ユースケース＋メタデータ案＋プロダクト案）を AssetVane のアーキに当てて選別した相談から確定した 4 件。前提＝ADR-044〜047（統合コーパス・意味検索・ユーザー入力・一覧画面）。スコープは設計判断（本 ADR）まで＝実装は別タスク。grill-me（`rag-grill-me-cheerful-sun`）で線引きを確定した。

## ADR-049: ニュース RAG の活用を線引きする＝AI は事実を解釈するだけで数値スコアは作らない

- **状況/問題**: 外部 AI が「ニュース DB を RAG 化したら何ができるか」を 7 ユースケース（銘柄要約・カタリスト検出・センチメント・銘柄比較・急騰落の理由説明・決算材料整理・仮説検証）＋メタデータ案＋プロダクト案として列挙した（`.tmp/20260607_ai_rag.txt`）。統合コーパス（[ADR-044](#adr-044-ニュースを統合コーパスと階層タグに集約し-get_news_context-で3層を必ず揃える)）と意味検索（[ADR-045](#adr-045-ニュース意味検索は段階導入する初手は-embedding-と-sqlite-vec最終は-fts5-ハイブリッド)）の**器**はできたが、「その器で何をやる/やらないか」が未確定。多くの案が暗に **AI による数値スコア付与**（センチメントスコア・確度・重要度・材料ランキング）を求めており、これは AssetVane の不変条件（[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け)/[ADR-016](#adr-016-手法はコードで実装する手法db-は索引でありコードの代替ではない)＝AI は数値を計算しない・quant 純関数が事実を出す）と正面衝突する。
- **検討して却下した案**:
  - **AI がニュースに 0〜1 の数値スコアを付与し signals/ランキングの数値根拠にする** → **却下**。LLM のスコアは非再現で、同じニュースでも実行ごとにブレ、backtest（[ADR-016](#adr-016-手法はコードで実装する手法db-は索引でありコードの代替ではない)）と signals の連続スコア体系（[ADR-026](#adr-026-signals-は連続スコアの材料ai-が主消費者で閾値は破壊的ゲートにしない)）を壊す。「材料ランキングを AI スコアで作る」も同根で却下。
- **決定**: ニュース RAG の活用を **「AI は事実を解釈するだけ・数値は quant が出す」**で線引きし、採用ユースケースを確定する。
  - **採用（解釈系・チャット Tool `get_news_context`/`search_news` 経由）**: ① 銘柄別ニュース要約（好/悪/中立は定性分類）／② カタリスト検出（「予測」でなく「来るイベントの列挙」に寄せる＝[ADR-009](#adr-009-自動売買はしない提示に徹する)）／⑤ 株価変動の理由説明（値動きの事実は quant の `momentum`/`volume_spike`、ニュースは理由付け）／⑦ 投資仮説の検証（`search_news` で支持/反証材料を集め AI が秤にかける）。
  - **採用（定性タグのみ）**: ③ センチメントは定性 `polarity`（例 `短期ポジ`/`長期ネガ` の enum）タグとして持つ。**数値 sentiment_score は持たない**。
  - **条件付き採用**: ⑥ 決算材料整理は「決算**後**」のみ（`financials.disclosed_date` を起点に直近決算後のニュース＋KPI を定性整理）。「決算**前**チェックリスト」は決算予定カレンダー（J-Quants 無料/Light での取得可否は未確認・TDnet 適時開示は有料アドオン）が前提条件のため**予約**。
  - **却下**: 確度・重要度・影響スコア・材料ランキングのスコア化（AI が数値を生む案は全部）。ランキングが要るときは quant の既存指標（`volume_spike`/騰落率）で並べ、ニュースはその順位の「理由」として添える。
  - **分離**: ④ 銘柄比較・テーマ株スクリーニング・競合比較は theme 概念を要するため [ADR-050](#adr-050-銘柄とニュースにテーマタグを持たせ語彙揺れをプロンプト照合と-embedding-近接で抑える)へ。能動配信（保有リスクアラート・急騰落の自動説明）は [ADR-051](#adr-051-ニュースとシグナルと保有を結ぶ能動配信を-notify_digest-に拡張する)、売買アイデア提示は [ADR-052](#adr-052-ニュース起点の売買アイデアは-proposals-の-buysell-に承認制で起票する)へ。
- **理由**: 器（コーパス・検索）と活用（何を AI にさせるか）を分けて記録することで、将来「便利そう」を理由に AI スコアを混ぜる逸脱を防ぐ。解釈系は AssetVane の中核（AI と相談しながら提示）にそのまま乗り、数値は一貫して quant が持つ。
- **段階**: docs 確定（線引き）。採用ユースケースは `get_news_context`/`search_news` の活用であり新規 schema を要さない。**定性 `polarity` 列は 2026-06-13 実装済み**（`news.polarity`〔`0020_news_polarity`・`down_revision=0019`〕・3 値 `positive`/`negative`/`neutral`・NULL=未判定・夜間 `tag_news_polarity` が `level='stock'` のみ判定＝[ADR-051](#adr-051-ニュースとシグナルと保有を結ぶ能動配信を-notify_digest-に拡張する) の能動配信の前提を解消）。[advisor.md](advisor.md) にユースケースを反映する。
- **関連**: [ADR-044](#adr-044-ニュースを統合コーパスと階層タグに集約し-get_news_context-で3層を必ず揃える)（活用の器）・[ADR-045](#adr-045-ニュース意味検索は段階導入する初手は-embedding-と-sqlite-vec最終は-fts5-ハイブリッド)（意味検索）・[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け)/[ADR-016](#adr-016-手法はコードで実装する手法db-は索引でありコードの代替ではない)（AI は数値を作らない＝活用面で具体化）・[ADR-026](#adr-026-signals-は連続スコアの材料ai-が主消費者で閾値は破壊的ゲートにしない)（signals は連続スコア・AI スコアを混ぜない）・[ADR-009](#adr-009-自動売買はしない提示に徹する)（提示専用）・[ADR-050](#adr-050-銘柄とニュースにテーマタグを持たせ語彙揺れをプロンプト照合と-embedding-近接で抑える)/[ADR-051](#adr-051-ニュースとシグナルと保有を結ぶ能動配信を-notify_digest-に拡張する)/[ADR-052](#adr-052-ニュース起点の売買アイデアは-proposals-の-buysell-に承認制で起票する)（派生）。

## ADR-050: 銘柄テーマは「実在テキストに grounded な全ユニバース事前タグ」で持つ（改訂・名前推測を禁じ EDINET/longBusinessSummary を信号源にする）

> **改訂（2026-06-10）**。初版（下記「旧決定（superseded）」）は「付与口を `investigate_stock` に寄せ・JP のみ・調査済みのみ・常時ジョブを増やさない」だった。だが**「未調査銘柄も米株もテーマで引きたい」**という確定要件が初版の中核決定と衝突したため、付与アーキテクチャを全面的に置き換える。語彙 reconcile（プロンプト照合＋embedding 近接）の骨子だけは存続させる。

- **状況/問題**: 活用案④（銘柄比較・テーマ株スクリーニング・競合比較）は、業種コードをまたぐ **テーマ**（"AI需要"・"防衛"・"円安メリット" 等）で銘柄を束ねたい。初版は付与口を `investigate_stock`（[ADR-020](#adr-020-個別銘柄ドシエ定性ファンダ調査-1銘柄1レポートを更新し続ける)）に寄せたが、これだと**テーマが付くのは調査済みの JP 銘柄だけ**になり、「未調査も米株も `screen_by_theme` で引きたい」要件を満たせない。全ユニバースをテーマ付けするには「銘柄を語る実在テキスト」が要るが、未調査銘柄に手元の信号は社名＋セクターしかない。
- **旧決定（superseded・初版 2026-06-07）**: 銘柄テーマを `investigate_stock` 時に AI が定性付与し銘柄×theme 台帳に持つ／付与口を `investigate_stock` に寄せ「新たな常時ジョブを増やさない」／JP のみ・調査済みのみ。→ **本改訂で置換**（全ユニバース事前タグへ）。「常時ジョブを増やさない」は付与口限定の理由付けだったが、要件を優先して撤回する。
- **検討して却下した案**:
  - **社名＋セクターだけを軽量 LLM に渡して全銘柄タグ付け** → 却下。**名前の字面（"日本〇〇"・"〇〇電機"）に引きずられハルシネーション地獄**になり使えないタグの山になる。タグは必ず実在テキストの根拠に当て、判定時は `code`/`symbol`（同一性）を渡し名前推測させない。
  - **embedding 近接だけで自動割り当て** → 却下。薄い JP 信号（社名＋セクター）の近接はノイズが多く誤タグが増える。
  - **未調査銘柄に ad-hoc web search を都度かけてタグ** → 却下。全銘柄分の検索は重く遅く、検索結果自体のノイズも残る。権威ある事業説明テキスト（EDINET/`longBusinessSummary`）を直接与えるほうが堅い。
  - **`stock_themes` に `source` 列＋source 別置換で書き手衝突を解く** → 却下（複雑）。UPSERT＋`last_seen_at` 時間窓 prune で source 列なしに「クロバー回避＋freshness」を両立する（下記）。
  - **テーマ語彙を自由放任／毎回人手承認制（pending lane）** → 却下（初版同様）。乱立はタグ爆発、承認は単一ユーザー（[ADR-001](#adr-001-単一ユーザー前提で作る)）に過剰。
- **決定**:
  - **全ユニバース（JP＋US）を実在テキストに grounded で事前タグ付けする**。付与は `investigate_stock` 限定をやめ、専用のタガー（バックフィル一括＋日次差分）が担う。**名前推測は禁止・`code`/`symbol` を同一性として必ず渡す・根拠が無ければタグを付けない**。
  - **信号源は compact プロフィールに統一**＝米株は `.info.longBusinessSummary` をそのまま（既に短い・[ADR-055](#adr-055-米株スクリーナーphase-7b-1は-yfinance-一本gics-は-yahoo-infosector-の文字列保持提示専用で-jpy-資産評価コアに触れない)）、JP 未調査は **EDINET 有報「事業の内容」**（[ADR-056](#adr-056-edinet-を-jp-の事業説明テキスト源にする有報事業の内容-を要約して-company_descriptions-に持つ)）を**まず要約**して compact 化（[ADR-020](#adr-020-個別銘柄ドシエ定性ファンダ調査-1銘柄1レポートを更新し続ける) の「取得→要約→本文捨てる」イディオム流用）。JP 調査済みは `investigate_stock` のドシエ/ニュースを**リッチなオーバーレイ**にする。保存・embedding・タガーは全て compact 版を読む。
  - **タガー＝grounded LLM 判定**＝入力（`code`/`symbol`＋compact プロフィール＋既存テーマ語彙）→出力（テキストが支持するテーマだけ・根拠引用付き）。実在テキストの定性分類であり数値を作らない（[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け)）。安いモデルで可（[ADR-012](#adr-012-llm-はアダプタで抽象化openrouter-既定ローカルへ差替可)）。
  - **データモデル**（[data-model.md](data-model.md)）: `themes` 目録（JP＋US 横断のグローバル語彙・`name` PK・`embedding`・`embed_model`・`first_seen_at`・`near_duplicate_of`）＋ `stock_themes` 台帳（`market`/`code`/`theme_name`/`first_assigned_at`/`last_seen_at`・`UNIQUE(market,code,theme_name)`・**cross-FK なし**＝`signals` と同じ生データ流儀・**source 列なし**）＋ `company_descriptions`（compact 実在テキスト・`source`/`doc_id`/`disclosed_date` は**テキストの provenance**）。
  - **書き込みは UPSERT＋`last_seen_at` bump（削除しない＝クロバーしない）**、古いタグは**時間窓 prune**（一定期間どの再タグにも再確認されなかった行だけ枯らす＝特定書き手基準でないのでクロバーにならない）。これでユニバースタガーと investigate オーバーレイの2書き手が共存する（**JP の実装上の扱いは下記「実装メモ（段階B・2026-06-11）」で reframe＝単一テキスト・dossier 優先・包含ゆえ共存不要**）。
  - **語彙 reconcile は目録層で**（表記揺れは `stock_themes` でなく `themes` で吸収）＝(1) 付与プロンプトに既存テーマ語彙を注入「該当あれば exact 再用」＋(2) embedding 近接（[ADR-045](#adr-045-ニュース意味検索は段階導入する初手は-embedding-と-sqlite-vec最終は-fts5-ハイブリッド) の `vec_distance_cosine` 流用）で重複候補を `near_duplicate_of` にフラグ（**自動マージはせず候補提示**）。閾値は定数・保守的既定・tunable。`embedding_enabled()` が False なら第二段 skip し embedding=NULL で degrade（[ADR-006](#adr-006-ml-学習は別pcラズパイは-pkl-で推論のみ)/[ADR-018](#adr-018-無人運用の障害時方針失敗を黙って放置しない)）。embedding 生成は夜間 `embed_themes`（`embed_news` 同型）に分離。
  - **コールドスタート**＝種テーマ（防衛・AI需要・半導体・円安メリット…30〜50 個）を `app/reference/`（[ADR-053](#adr-053-sector17-の二体系分類-s17--銘柄-etf-ティッカーの境界を固定し業種コード参照知識を-appreference-に集約する) の参照知識層）に置き初回に目録へ仕込む。有機的な新テーマ追加は種の上に育つ。
  - **差分の「変化」定義**＝即時（未タグ＋説明テキストが前回タグ以降に変化した銘柄だけ再タグ）＋緩やか（最終タグが古い順に夜あたり N 件のローテ＝[ADR-033](#adr-033-銘柄ごとの調査-cadence-は-interval_days-夜あたり天井で律速する) cadence 流用で語彙ドリフトを eventual 追従）＋手動フル再タグ（script/`/settings`）。
  - **消費 Tool 3 本**（[advisor.md](advisor.md)）: `list_themes`（語彙＋件数＋near_dup フラグ）・`get_stock_themes(market, code)`・`screen_by_theme(theme, market?, sector17_code?, limit?)`。**競合比較は専用 Tool を作らず合成**（get_stock_themes→screen_by_theme(theme, 同セクター)）。戻り値はテーマ所属の事実のみ＝バリュエーション数値を持たせない（[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け)）。
- **理由**: テーマを「実在テキストに grounded な定性タグ＋目録での語彙 reconcile」で持てば、名前推測のハルシネーションを断ちつつ全ユニバースを覆える。UPSERT＋時間窓 prune は「source を持たない／freshness／2書き手のクロバー回避」の三択トレードオフを同時に満たす唯一解。付与口を `investigate_stock` に限定しないことで未調査・米株も引けるようになり、初版の制約を解く。
- **段階**: **docs 確定（改訂）**。実装は段階で別タスク＝**A**: 米株を `longBusinessSummary` で grounded タグ＋目録＋reconcile＋Tool（EDINET 不要・最速で「テーマで引く」完成・**2026-06-10 実装済み**）／**B**: investigate_stock の JP 調査済みにオーバーレイ（**2026-06-11 実装済み・下記実装メモ**）／**C**: EDINET アダプタ→JP 全ユニバース＋backfill＋差分＋`/settings`（**2026-06-11 実装済み・下記実装メモ**）。[data-model.md](data-model.md)/[advisor.md](advisor.md)/[roadmap.md](roadmap.md) に同期。
- **実装メモ（段階C・2026-06-11）**:
  - **取得モデルは提出日クロール型**（grill 確定）＝EDINET 書類一覧 API は提出日でしか引けないため、銘柄単位の「最新有報」解決はクロールの帰結。カーソルは `fetch_meta('edinet:crawl')` の `last_fetched_date` 1 本（「最後に**完了**した提出日」）。クロール基本動作（`crawl`）は 1 個に統一し、夜間差分（`fetch_edinet_descriptions.run`＝X=カーソル翌日）とバックフィル script（`app.scripts.backfill_edinet`＝X=今日−約15ヶ月窓）が起点だけ変えて同じ core を呼ぶ。**storage は migration 不要**＝`company_descriptions` の `source`/`disclosed_date`/`doc_id` 列（0018）で足り、`stocks.code`（5桁）と EDINET `secCode` は直接一致（ユニバース外提出は skip）。
  - **EdinetAdapter（`adapters/edinet.py`・IO 専用）**＝`list_documents(date)`（書類一覧 type=2・外部キー名→内部名を閉じる）＋`fetch_business_description(doc_id)`（取得 type=5＝CSV ZIP・UTF-16 タブ区切りから `jpcrp_cor:DescriptionOfBusinessTextBlock` を抜き HTML strip）。要約は IO に置かず `advisor/edinet_summary.summarize_business_description`（`generate_once` 単発・具体名詞保持で grounding を守る・ADR-020/014）。
  - **dossier 優先は 2 段ガード**＝① クロールで secCode を拾った時、要約 LLM を撃つ前に「既存 `source='dossier'` は skip／既存 edinet の `disclosed_date` が今回の `period_end` 以上なら skip」（コスト節約）＋② repo の `upsert_company_description_edinet`（`protect_dossier=True`）が on_conflict WHERE に `source != 'dossier'` を足して既存 dossier を上書きしない（保険）。dossier 書き込み（W2・段階B）は無条件のまま勝つ。docTypeCode は 120（有報）のみ＝訂正報告 130 は対象外（次年度有報が上書き・既知の割り切り）。
  - **NIGHTLY 順** `investigate_dossier` → **`fetch_edinet_descriptions`** → `tag_jp_themes` → `embed_themes`（JP の description 書き手を dossier→edinet の優先順に並べ、直後に `tag_jp_themes` が `source` 不問で拾う＝既存ジョブ無改変）。`/settings` は runner から抽出した `run_jobs([fetch_edinet_descriptions, tag_jp_themes])` を `POST /edinet/run-differential` でオンデマンド起動（夜間と同じ lock/state/通知＝ADR-011/036）。重い 15ヶ月バックフィルと無キャップ一括タグは app.scripts 手動のまま（コストガード）。
- **実装メモ（段階B・2026-06-11）**:
  - **経路は company_descriptions 経由（段階A 対称）**＝`investigate_stock` がドシエ要約 `summary_md` を `company_descriptions(JP, source='dossier')` に **W2**（`upsert_company_description_tx`・ドシエ本体と同一 conn で atomic）で焼く。夜間 `tag_jp_themes`（`tag_us_themes` 対称の独立ジョブ・`list_jp_codes_for_theme_tagging` 起点・`prune` は `market='JP'` 限定）が既存 `tag_stock_themes(market='JP')` を**無改変で再利用**してタグ付けする。NIGHTLY 順は `investigate_dossier`（run_advisor 後・JP 信号源を書く）→ `tag_jp_themes` → `embed_themes`。信号源は `summary_md` そのまま（既にニュース要約＋財務事実を織り込むため「ドシエ＋ニュース」両根拠を兼ねる・追加 LLM コストなし）。
  - **「2書き手共存」の reframe**: `company_descriptions` の UNIQUE は `(market, code)`＝**1銘柄1テキスト**で全市場共通（US も `yfinance` 1ソースのみ）。段階Bを company_descriptions 経由にしたことで、ユニバースタガー（段階C・EDINET）と investigate オーバーレイ（段階B・dossier）は**同一の1テキスト枠を共有**＝独立2書き手にはならない。調査済み JP は **dossier 優先**（dossier は EDINET「事業の内容」＋ニュース＋財務から組まれ EDINET を包含＝**dossier ⊇ EDINET なのでユニバースタグを失わない**）。段階C 実装時は「dossier 行があれば edinet で上書きしない」で解く。**よって prune の役割は JP では「②信号源テキストが変わって LLM が確認しなくなったタグを時間窓で枯らす」だけが効き、「①多書き手の減衰オーバーレイ」は使わない**。初版の「2書き手共存」は JP には過剰だったと判明＝**「単一テキスト・dossier 優先・包含ゆえ共存不要」に置換**する（US も同様に1ソースなので全市場で一貫）。
  - **毎晩 LLM 再タグの最適化（段階B 固有）**: 調査済み母集団は小さく天井に対し毎晩ほぼ全件がローテ選定されるため、説明テキストが前回タグ以降に未変化なら LLM を呼ばず `bump_stock_themes_last_seen` で `last_seen_at` だけ bump する（prune 回避の安価パス）。語彙ドリフト追従を全件 LLM 再タグで担う段階A US（`tag_us_themes`）とは挙動が割れるが意図的（`theme_tagging_jp_nightly_max`＝既定 100・別枠）。
  - **un-investigate でタグは減衰させない**: watchlist から外しても `company_descriptions(JP,dossier)` は残し、ローテが永続 bump するのでタグは保持される（「もう見ない銘柄」のタグも残す方針＝ユーザー確定）。
  - **near_duplicate_of は段階Bでも「フラグのみ」**: 自動マージ・query 時クラスタ展開・実マージ工程はいずれも入れない（初版どおり候補提示のみ＝[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け) の規律を保つ）。語彙統合の主役は付与プロンプトの「既存語彙 exact 再用」で、embedding は保険。
- **関連**: [ADR-056](#adr-056-edinet-を-jp-の事業説明テキスト源にする有報事業の内容-を要約して-company_descriptions-に持つ)（JP 信号源）・[ADR-055](#adr-055-米株スクリーナーphase-7b-1は-yfinance-一本gics-は-yahoo-infosector-の文字列保持提示専用で-jpy-資産評価コアに触れない)（米株 `longBusinessSummary`）・[ADR-045](#adr-045-ニュース意味検索は段階導入する初手は-embedding-と-sqlite-vec最終は-fts5-ハイブリッド)（embedding 流用）・[ADR-053](#adr-053-sector17-の二体系分類-s17--銘柄-etf-ティッカーの境界を固定し業種コード参照知識を-appreference-に集約する)（reference 層に種テーマ）・[ADR-033](#adr-033-銘柄ごとの調査-cadence-は-interval_days-夜あたり天井で律速する)（差分ローテ cadence）・[ADR-020](#adr-020-個別銘柄ドシエ定性ファンダ調査-1銘柄1レポートを更新し続ける)（要約イディオム・調査オーバーレイ）・[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け)（定性付与・数値でない）・[ADR-012](#adr-012-llm-はアダプタで抽象化openrouter-既定ローカルへ差替可)（タガーは安いモデル可）・[ADR-049](#adr-049-ニュース-rag-の活用を線引きするai-は事実を解釈するだけで数値スコアは作らない)（④の親）。

## ADR-051: ニュースとシグナルと保有を結ぶ能動配信を notify_digest に拡張する

- **状況/問題**: 活用案の「急騰落理由の自動説明」「保有銘柄リスクアラート」は、聞かれてから答える受動（チャット Tool）でなく**先回りして通知**したい。Phase 6 の `notify_digest`（夜間バッチ末尾・Discord 1 通に signals＋夜AI提案を束ね `notifications`/`send_once` で冪等＝[phase6-spec.md](phase6-spec.md)）は既にあるが、(a) signals に「なぜ動いたか」のニュースが付かない、(b) digest は**保有（`holdings`）と非連動**で全シグナルを社名表示するだけ。
- **検討して却下した案**:
  - **モーニングレポート・材料ランキングまでフル能動配信** → 予約。毎晩の LLM コストと通知ノイズが増え、単一ユーザー（[ADR-001](#adr-001-単一ユーザー前提で作る)）の可処分注意を超える。まず価値の高い 2 つに絞る。
  - **全部チャット受動のみ（能動を入れない）** → 却下。保有銘柄の悪材料は「気づけるか」が価値で、受動だと取りこぼす。
- **決定**:
  - **急騰落の自動説明**＝夜間に `volume_spike`/`momentum` の発火シグナルへ該当ニュース（`get_news_context` の銘柄層）を attach し、「なぜ動いたか」を digest に添える（値動きの事実は quant、説明はニュースの引用＝[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け)）。
  - **保有リスクアラート**＝`holdings` に紐づく銘柄の悪材料ニュース（定性 `polarity` が負＝[ADR-049](#adr-049-ニュース-rag-の活用を線引きするai-は事実を解釈するだけで数値スコアは作らない)）を抽出し digest に通知。`notify_digest` に holdings 紐づけを追加する。提示専用（[ADR-009](#adr-009-自動売買はしない提示に徹する)）・冪等は既存 `notifications`/`send_once` を踏襲。
- **理由**: 能動の価値が最も高い「保有の悪材料」「動いた理由」を、既存の Phase 6 digest 基盤に**最小拡張**で乗せる。新通知チャネルや新ジョブ骨格を作らず、attach と holdings フィルタの追加に留める。
- **段階**: **実装済み（2026-06-13）**。pytest green。実装＝(1) `news.polarity` 列〔`0020_news_polarity`・[ADR-049](#adr-049-ニュース-rag-の活用を線引きするai-は事実を解釈するだけで数値スコアは作らない)〕／(2) 夜間ジョブ `tag_news_polarity`〔`batch/jobs/tag_news_polarity.py`・`embed_news` 同型・`advisor/news_polarity.classify_polarities` で `level='stock'` の未判定行を 3 値バッチ判定・LLM 例外/総崩れで `ok=False`＝`embed_news` と契約対称・C-7／NIGHTLY 順は `investigate_dossier`→`embed_news`→**`tag_news_polarity`**→`embed_themes`→…→`notify_digest`〕／(3) `notify_digest` 拡張＝**①急騰落の自動説明**〔注目シグナル各行へ該当 `code` の直近 3 日 stock 層ニュース 1 件を attach・holdings 非依存・`list_news` 再利用〕＋**②保有銘柄の悪材料アラート**〔JP holdings の `polarity='negative'`・`fetched_at` 24h 窓で最大 5 件＋残件数・`list_negative_stock_news_for_codes`・能動配信の主眼ゆえ注目シグナルより前に置き 1900 字截断から保護・`has_content` に含め悪材料がある夜は `always_daily_digest=False` でも送る〕。冪等は既存 `notifications`/`send_once`（`digest:<UTC日付>`）を踏襲・migration は列追加 1 本のみ。[phase6-spec.md](phase6-spec.md) と `batch-pattern` スキルに同期。
- **関連**: [phase6-spec.md](phase6-spec.md)（Phase 6 拡張）・[ADR-049](#adr-049-ニュース-rag-の活用を線引きするai-は事実を解釈するだけで数値スコアは作らない)（⑤採用・polarity）・[ADR-026](#adr-026-signals-は連続スコアの材料ai-が主消費者で閾値は破壊的ゲートにしない)（signals は材料）・[ADR-009](#adr-009-自動売買はしない提示に徹する)（提示専用）・[ADR-044](#adr-044-ニュースを統合コーパスと階層タグに集約し-get_news_context-で3層を必ず揃える)（get_news_context）・[ADR-001](#adr-001-単一ユーザー前提で作る)（単一ユーザー・ノイズを抑える）。

## ADR-052: ニュース起点の売買アイデアは proposals の buy/sell に承認制で起票する

- **状況/問題**: AssetVane の核は「AI と相談しながら銘柄・配分を提示」（[ADR-009](#adr-009-自動売買はしない提示に徹する)/[ADR-011](#adr-011-ai-advisor-を-2-軸夜の分析ai相談チャットaiで実装する製品の核心)）。活用案「ニュース起点の売買アイデア生成」をこの提示専用の枠に乗せたい。`proposals` テーブルは `kind` に `buy/sell/rebalance` を定義済みだが、起票・承認ロジックは `policy_change` のみ実装で buy/sell は未実装（schema 定義のみ）。
- **検討して却下した案**:
  - **`submit_journal` の proposal テキストに自由文で書くだけに留める** → 却下。構造化されず、承認/不採用・結果（`outcome`）の追跡ができない。
  - **スコープ外にする** → 却下。ニュース 3 層文脈（[ADR-044](#adr-044-ニュースを統合コーパスと階層タグに集約し-get_news_context-で3層を必ず揃える)）が乗ると「なぜ買い/売りか」の根拠が強くなり、提示専用の本筋にむしろ近づく。
- **決定**: ニュース 3 層文脈（`get_news_context`）を根拠に、Advisor が buy/sell 提案を `proposals`（`kind=buy/sell`）へ起票する。**承認制・約定はしない**（[ADR-009](#adr-009-自動売買はしない提示に徹する)）。既存 `policy_change` lane と同型で、`depends_on` の承認順制御・`/proposals/{id}/approve|reject` を流用する。**数値（株数・金額）を AI に計算させず**、提案は「方向と根拠」に留め、サイズは別途 portfolio 最適化（[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け) の事実計算）に委ねる。
- **理由**: 売買アイデアは提示専用の自然な終点で、既存の承認制 proposals 設計に予約済みの lane を起こすだけ。ニュース RAG の出口を「追跡可能な提案」にすることで、journal の自由文より検証可能になる。
- **段階**: **実装済み（2026-06-11）**。pytest green。実装＝**専用 Tool `propose_trade(action, code, reason)`**（`min_phase=4`・`get_news_context` と同じゲート）を新設。`submit_journal` と同じ**検証 only**契約（W2）で、handler（`handle_propose_trade`）は引数検証＋銘柄解決（JP→US）して AI に `{ok, company_name, market}` を返すだけ。**実起票は `persist_trade_proposals_from_tool_runs`**（`journaling.py`）が tool_runs から `propose_trade` 呼び出しを**全件**拾い、`begin()` 境界内で `insert_proposal(kind=buy/sell)` する＝夜AI（`nightly.py`）と昼チャット（`router.py`・**submit 無しでも独立起票**だが journal は明示 submit 時のみ＝ADR-029 保持）の両方が通る共通経路。**body=`{code, company_name, market}`・rationale=reason・数値ゼロ**。銘柄は `stocks`→`us_stocks` で解決し**未知コードは起票せず drop**（幻覚/誤記を queue に入れない＝[ADR-018](#adr-018-無人運用の障害時方針失敗を黙って放置しない)）。**pending のみ dedup**（同一 (kind, code) の pending があればスキップ・reject/approve 済みは状況変化後の再提案を通す）。**depends_on は None**（自動リンクなし・インフラは将来用に温存）。承認側は無改修（`resolve_proposal` は `policy_change` 以外は status 遷移のみ＝約定なし既実装）。frontend は `ProposalCard` の `bodySummary` を磨いて「会社名（code・market）」表示。`rebalance` 起票・株数/金額・承認後の保有反映は**非スコープ**。migration 不要（proposals 既存列で表現）。[phase3-spec.md](phase3-spec.md)/[advisor.md](advisor.md)/[data-model.md](data-model.md) に同期。
- **関連**: [ADR-009](#adr-009-自動売買はしない提示に徹する)（提示専用・承認制）・[ADR-011](#adr-011-ai-advisor-を-2-軸夜の分析ai相談チャットaiで実装する製品の核心)/[ADR-013](#adr-013-投資方針-policy-は単一チャットで育てる版管理機構は作らない)（2軸AI・単一policy）・[ADR-049](#adr-049-ニュース-rag-の活用を線引きするai-は事実を解釈するだけで数値スコアは作らない)（ニュース根拠）・[ADR-044](#adr-044-ニュースを統合コーパスと階層タグに集約し-get_news_context-で3層を必ず揃える)（3層文脈）・[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け)（株数はAIに計算させない）。

---

## ADR-053: sector17 の二体系（分類 S17 ／ 銘柄 ETF ティッカー）の境界を固定し業種コード参照知識を app/reference に集約する

- **状況/問題**: [ADR-044](#adr-044-ニュースを統合コーパスと階層タグに集約し-get_news_context-で3層を必ず揃える)で実装した `get_news_context` の**セクター層が本番データで常に空**になるバグが発覚した。`build_news_context`（`services/news.py`）は銘柄の `stocks.sector17_code`（J-Quants S17 分類＝"1".."17"・ETF/REIT は "99"）で `news` を等値 JOIN するが、夜間ジョブ `fetch_sector_news`（`adapters/news.py`）は `news.sector17_code` を "1617".."1633"（TOPIX-17 セクター ETF の**銘柄ティッカー**体系）でタグ付けしていた。"6" ≠ "1622" で**永久不一致**＝セクター層が空・和名も `None`（stock 層・market 層は正常）。ADR-044 実装時に、分類コードと ETF ティッカーという**二つの体系を取り違えた**のが原因。稼働コンテナで実証済み（`news` テーブルは現状 **0 行**＝String 列で migration 不要・毎晩再取得される ephemeral データ）。
- **検討して却下した案**:
  - **ニュースのタグも ETF ティッカー "1617".."1633" に寄せる（JOIN 側を変換する）** → 却下。ニュースの `sector17_code` が表すのは「その記事はどの業種の話か」という**分類**であって、実在 ETF を指す instrument ではない。分類を ETF ティッカー空間に持ち込むと、住所が「対象」ではなく「参照先の道具」で割れ、`stocks.sector17_code` との素直な一致が失われる。
  - **lead_lag 側も S17 に寄せて全体を S17 に統一する** → 却下。`quant/lead_lag.py` の `JP_SYMBOLS` は**実在する業種 ETF の株価を引く** instrument であり、ここを S17 に変えると別銘柄の株価を参照し lead_lag が壊れる。二体系は本質的に別物で、無理に一本化しない。
- **決定**:
  - **境界を固定する**＝classification（分類）＝J-Quants S17 "1".."17" ／ instrument（銘柄）＝ETF ティッカー "1617".."1633"。
  - **ニュースのセクタータグは「分類」なので S17 に寄せ、`stocks.sector17_code` と直接一致させる**（変換なしの等値 JOIN）。`fetch_sector_news` も stocks と同じ S17 でタグ付けする。
  - **lead_lag は実在 ETF の株価を引く「instrument」なので ETF ティッカーのまま一切不変**（`quant/lead_lag.py` の `JP_SYMBOLS`・`services/lead_lag.py` の `_to_db_code`・`signals.code`・`GET /lead-lag`・frontend は ETF 空間のまま）。
  - **両空間の対応表（ETF ＝ 1616 + S17・全 17 業種で成立確認済み）と和名ラベルを `app/reference/` に SSOT 集約**する。マッピングは明示 dict で持ち、`1616+N` のマジック式（並び順前提の暗黙ルール）に依存しない。
  - **`app/reference/` を参照知識専用の第三カテゴリとして新設**＝IO 無し・副作用無し・標準ライブラリのみ依存。adapters（外部 IO）でも domain（業務ロジック）でもない「参照知識」を正しく名付ける（`general_news_config.py` のコメントが既に「業種コードは接続情報でない参照知識」と区別しており、その思想に一致）。依存規約は「**全レイヤ → reference は OK／reference → 他層は禁止**」（序列の最内・中立横断＝`config.py`・`logging_config.py` と同じ中立点）。
- **確定した不変条件**: `quant/lead_lag.py` の `JP_SYMBOLS`・`_to_db_code`・`signals.code`・`GET /lead-lag`・frontend は **ETF 空間のまま不変**。migration は作らない（`news` 0 行・String 列でスキーマ変更不要・ephemeral）。
- **理由**: 「分類はニュースの対象を表すタグ／instrument は実在 ETF を引く道具」という**意味の違い**に沿って二体系の境界を固定すれば、ニュースは `stocks` と直接一致し、lead_lag は実在 ETF の株価を正しく引ける。両者の対応を `app/reference/` に SSOT 集約することで、コード表を adapters に「写経」して取り違える再発を断つ（[ADR-010](#adr-010-データソースはアダプタ越しにする) のアダプタは外部 IO 専用で、純粋な参照知識はそこに置かない）。
- **採否経緯**: Codex ×2 と設計エージェントが独立に同結論（S17 canonical ＋ `app/reference` 集約・lead_lag 不変）に至った。
- **段階**: docs 確定＋実装（別タスク）＝`app/reference/sector_codes.py`（マッピング dict＋純関数）・`adapters/general_news_config.py`／`adapters/news.py`／`services/news.py`／`services/lead_lag.py` の張り替え・`db/schema.py` コメント修正・テスト修正＋新規 `test_sector_codes.py`。[data-model.md](data-model.md) に同期（stocks／news の `sector17_code` 説明・取り込みジョブ）。
- **関連**: [ADR-044](#adr-044-ニュースを統合コーパスと階層タグに集約し-get_news_context-で3層を必ず揃える)（バグの発生元・本 ADR で体系を確定）・[ADR-039](#adr-039-phase-7-を-a-sector-lead-lag-先行b-米株拡張に分割し-a-の業種-etf-は-indexadapter-に-yahoo-ソースを足して流用する)（sector17/lead_lag の ETF ティッカー）・[ADR-034](#adr-034-一般ニュースダイジェスト銘柄に紐づかないニュースを別系統で持つ実装済み)（`general_news_config.py`・参照知識の区別）・[ADR-010](#adr-010-データソースはアダプタ越しにする)（アダプタは外部 IO 専用・参照知識は別）・[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け)/[ADR-016](#adr-016-手法はコードで実装する手法db-は索引でありコードの代替ではない)（事実はコード・参照知識は安定資産）。

## ADR-054: 投資信託（非上場投信）を専用テーブルで保有管理し含み損益を随時計算する（external_assets と分離）

- **状況/問題**: 利用者は楽天証券で **eMAXIS Slim 全世界株式（オール・カントリー）** と **楽天・ゴールド・ファンド**（いずれも非上場の公募投信）を保有しており、基準価額（NAV）を日次取得して含み損益を**随時**見たい。現状の投信の置き場は `external_assets`（[ADR-010](#adr-010-データソースはアダプタ越しにする)・[data-model.md](data-model.md)）＝「全体に対する割合だけ AI に把握させる軽量記録・評価額は手入力・深追いしない」だった。これは「割合文脈で深追いしない」という当初方針に沿った設計だが、NAV を引いて取得単価との差から損益を自動計算する用途には合わない（手入力評価額では随時の含み損益が出せない）。株（`stocks`/`daily_quotes`/`transactions`/`holdings`）は取引ベースで保有・損益を導出する仕組みが既にある（[ADR-019](#adr-019-保有は取引から導出する-holdings-は-transactions-の射影)）のに、投信だけがその枠外にいた。
- **検討して却下した案**:
  - **`external_assets` を拡張して NAV と取得単価を相乗りさせる** → 却下。`external_assets` は「軽量・割合文脈・1 行＝評価額手入力」という役割で意図的に薄く作ってある（[ADR-010](#adr-010-データソースはアダプタ越しにする)）。ここに取引履歴・口数・移動平均取得単価・NAV 時系列を足すと、薄い記録という役割が崩れ、株側の取引ベース導出（[ADR-019](#adr-019-保有は取引から導出する-holdings-は-transactions-の射影)）と二重実装になる。投信は別の重さの存在として独立させる。
  - **投信を `stocks`/`daily_quotes` に混ぜる**（上場 ETF と同列に扱う）→ 却下。非上場投信は証券コードでなく **ISIN/協会コード**で識別し、四本値でなく **1 日 1 本の NAV（基準価額）**を持つ。価格の単位も「10,000 口あたりの円」で株価とは別物。混ぜると識別子・価格単位・取得元（J-Quants は[日本株専用](#adr-008-j-quants-は-v2x-api-key-を使うv1-は使わない)で投信 NAV を返さない）がすべて食い違う。
  - **NAV 取得元に各運用会社サイトのスクレイピングや有料 API を使う** → 却下。**投信総合検索ライブラリー**（ウエルスアドバイザー運営）が **ISIN 指定の CSV** を全公募投信で同一フォーマット・遅延なしの実値で提供しており、これを使えば 1 経路で両ファンドを賄える。
- **決定**:
  - **投信専用テーブルを 4 つ新設**し、株の `stocks`/`daily_quotes`/`transactions`/`holdings` を**ミラー**する（`funds`／`fund_navs`／`fund_transactions`／`fund_holdings`・列詳細は [data-model.md](data-model.md)）。`external_assets` には混ぜない。
  - **識別子は ISIN を主キー**にする（NAV 取得が ISIN 必須のため）。協会コードは表示用の任意列に留める。
  - **NAV 取得元は投資信託総合検索ライブラリーの CSV**（ISIN 指定・全公募投信同一フォーマット・遅延なし実値）。アダプタ越しに取る（[ADR-010](#adr-010-データソースはアダプタ越しにする)）。
  - **単位はすべて「10,000 口あたりの円」**（基準価額・取得単価・口数の換算基準）。評価額 = `units / 10000 * nav`、含み損益 = `units / 10000 * (nav - avg_cost)`。AI には計算させず、Python が事実を出す（[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け)）。
  - **入力は株と同じ取引ベース**＝`fund_transactions`（buy/sell）を記録し、口数と移動平均取得単価を `fund_holdings` に自動導出する（[ADR-019](#adr-019-保有は取引から導出する-holdings-は-transactions-の射影)と同型・mutation 後に atomic 再計算）。
  - **画面は portfolio に専用「投資信託」セクション**を設け、`asset-overview` に **`fund_value` バケット**を新設して total／pnl／配分パイに独立スライス（「投資信託」）として合算する（`asset_snapshots` に `fund_value` 列を追加）。
  - **周辺連携は最小限を今回含める**＝AI Advisor Tool `get_fund_holdings`（投信保有の事実を返す）と NAV 推移チャートまで。**`/optimize`（PyPortfolioOpt 最適化）への投信組み込みは見送り**。
  - **二重計上の回避は手動**＝既存 `external_assets` に置いていたオルカン等は、利用者が手動で削除する。移行コードは書かない。
  - **「投信は割合文脈で深追いしない」という当初方針（[ADR-010](#adr-010-データソースはアダプタ越しにする)）を、本 ADR で意図的に上書きする**。深追いするのは保有 2 ファンドの「含み損益の随時計算」という具体目的があるため。
- **理由**: 投信を株と同じ「取引ベース＋日次価格＋導出保有」の形に揃えれば、含み損益・評価額・資産全体への合算が株とまったく同じ規律（[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け)＝事実は Python・[ADR-019](#adr-019-保有は取引から導出する-holdings-は-transactions-の射影)＝取引から導出）で出せる。`external_assets` の「軽量・割合文脈」という役割を壊さず、投信だけ別の重さで独立させることで、両者の責務が混ざらない。
- **将来課題（落とさない・明示管理）**:
  - **自動積立**: 毎月の積立を `fund_transactions` の buy として自動生成する仕組みは今回作らない。当面は積立分も buy として手入力する。
  - **`/optimize`・相関分析への投信組み込み**: 平均分散最適化・相関ヒートマップ（[ADR の Phase 2 群]）に投信を載せるのは見送り。NAV 時系列が溜まり相関を語れるようになってから再検討。
  - **`external_assets` からの自動データ移行はしない**（手動削除）。移行コードを書かない判断は本 ADR で確定済みで、将来も自動移行は予定しない。
- **段階**: docs 確定（本 ADR）。コードは別タスクで実装中（[data-model.md](data-model.md)／[api.md](api.md)／[roadmap.md](roadmap.md) に同期）。
- **関連**: [ADR-010](#adr-010-データソースはアダプタ越しにする)（external_assets の「軽量・割合文脈」を本 ADR で上書き・NAV はアダプタ越し）・[ADR-019](#adr-019-保有は取引から導出する-holdings-は-transactions-の射影)（取引ベース導出・mutation 後 atomic 再計算）・[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け)（含み損益は Python が計算）・[ADR-008](#adr-008-j-quants-は-v2x-api-key-を使うv1-は使わない)（J-Quants は日本株専用＝投信 NAV は別経路）・[ADR-001](#adr-001-単一ユーザー前提で作る)（単一ユーザー・移行は手動で足りる）。

## ADR-055: 米株スクリーナー（Phase 7(B-1)）は yfinance 一本・GICS は Yahoo `.info.sector` の文字列保持・提示専用で JPY 資産評価コアに触れない

- **状況/問題**: [ADR-039](#adr-039-phase-7-を-a-sector-lead-lag-先行b-米株拡張に分割し-a-の業種-etf-は-indexadapter-に-yahoo-ソースを足して流用する) で Phase 7(B)（米株拡張）を「米株スクリーナー `/us-stocks`・米国個別株 OHLCV・米国ファンダ源・`UsEquityAdapter` 新設・通貨/FX 波及・GICS」と定義し繰り延べた。これを着工するにあたり (B) はまだ重い。**スクリーナー（提示）と、保有の通貨波及（FX 換算・holdings/cash/asset_snapshots への影響）は重さが桁違い**で、後者は JPY 単一前提の資産評価コア（[ADR-031](#adr-031-株式スクリーナー夜間-valuation_snapshots-読み取り時ランク市場ごとに分離) の通貨ポリシー）を全面的に触る。一括で進めると軽い提示まで重い通貨波及に引きずられる。あわせて (a) 米株のデータ源（OHLCV・ファンダ・ユニバース）をどこから取るか、(b) GICS 業種をどこまで厳密に追うか、を確定する必要があった。
- **決定**:
  - **(B) を (B-1) 米株スクリーナー（提示専用）と (B-2) FX/保有波及に分割し、(B-1) を先行する**（[ADR-039](#adr-039-phase-7-を-a-sector-lead-lag-先行b-米株拡張に分割し-a-の業種-etf-は-indexadapter-に-yahoo-ソースを足して流用する) の (A)/(B) 分割と同じ姿勢）。**(B-1) は提示専用＝既存 JPY 資産評価コア（holdings/cash/asset_snapshots/portfolio metrics/`/optimize`）には一切触れない**。FX 換算・米株保有登録・通貨列は (B-2) 送り（[ADR-009](#adr-009-自動売買はしない提示に徹する) 提示専用と整合）。
  - **データ源は yfinance 一本**。[ADR-039](#adr-039-phase-7-を-a-sector-lead-lag-先行b-米株拡張に分割し-a-の業種-etf-は-indexadapter-に-yahoo-ソースを足して流用する)(B) が明言した `UsEquityAdapter` を新設する（`backend/app/adapters/us_equity.py`）。`UsEquitySource` ABC（2 メソッド `fetch_quotes`/`fetch_fundamentals`）＋ `YahooUsEquitySource` ＋ファサード `UsEquityAdapter`（関心別フォールバック連鎖・`UsEquityNotSupported` を握る・`settings.us_equity_source_list` ＋ `_REGISTRY` で構築・今は yahoo のみ）＋ NASDAQ Trader directory パーサ `fetch_universe`。[IndexAdapter](architecture.md) と同型のフォールバック連鎖ファサード（[ADR-010](#adr-010-データソースはアダプタ越しにする)）。
  - **市場は物理的に別テーブル**（[ADR-031](#adr-031-株式スクリーナー夜間-valuation_snapshots-読み取り時ランク市場ごとに分離) 市場分離）。`us_stocks`／`us_daily_quotes`／`us_valuation_snapshots`（migration `0017_us_equity`）。日本株コア（stocks/daily_quotes/valuation_snapshots）と列はミラーするが `code→symbol`・`sector33_code→gics_sector` に読み替える。**currency 列は持たない**（(B-2) 送り）。
  - **業種は Yahoo `.info.sector`（GICS 相当 11 分類の英語ラベル）を `gics_sector` 文字列で保持**し `industry` を補助に持つ。**厳密な GICS コード体系は追わない**（和訳表は `backend/app/reference/gics_sectors.py`）。スクリーナーの業種絞り込みは `gics_sector` の完全一致、業種内パーセンタイル（`gics_sector_pctile`）も `gics_sector` 単位。
  - **valuation 列は [ADR-048](#adr-048-銘柄バリュエーションroeperpbr基準を-tool事実参照知識カードで持たせる) を踏襲**（PER/PBR/時価総額/配当利回り/ROE/営業利益率/純利益率/各 YoY 成長率＋市場内ランク）。**派生比率は日本株と同じ `quant/valuation.py` の純関数で読み取り時に Python 計算**（保存済み決算素 × 最新 close・通貨非依存）。window ランク（`gics_sector_pctile`・`market_cap_rank`）も読み取り時（[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け)/[ADR-016](#adr-016-手法はコードで実装する手法db-は索引でありコードの代替ではない)/[ADR-026](#adr-026-signals-は連続スコアの材料ai-が主消費者で閾値は破壊的ゲートにしない)）。
  - **夜間 4 ジョブ**（`NIGHTLY_JOBS`・`snapshot_assets` の後・通知系の前の独立ブロック）: `sync_us_universe`（NASDAQ Trader directory → `us_stocks`・**普通株のみ巡回**・優先株/ユニット/権利等は名称で除外・ETF は `is_etf=1` でフラグ保持）→ `fetch_us_quotes`（`yf.download` バッチ一括 → `us_daily_quotes` 全履歴・共通カーソル `fetch_meta['us_daily_quotes']`・初回 full backfill/以降差分）→ `fetch_us_fundamentals`（`.info` を per-symbol で `fetch_meta` 古い順＋夜天井 `settings.us_fundamentals_nightly_max=900` でローテ巡回＝[ADR-033](#adr-033-銘柄ごとの調査-cadence-は-interval_days-夜あたり天井で律速する) 同型・約 6000 銘柄を約 7 夜で一周）→ `calc_us_valuation`（quant 純関数再利用で `us_valuation_snapshots` を焼く）。各ジョブは部分失敗を握る（[ADR-018](#adr-018-無人運用の障害時方針失敗を黙って放置しない)）。
  - **AI Tool 2 つ**: `get_us_valuation`／`screen_us_valuation`（`min_phase=7`・返り値に `market:"US"`/`currency:"USD"` を明示・verdict は返さず LLM が判断＝[ADR-048](#adr-048-銘柄バリュエーションroeperpbr基準を-tool事実参照知識カードで持たせる) の (4) 契約をミラー）。**日本株 Tool（`get_valuation`/`screen_valuation`・JPY）は無改変**。`CURRENT_PHASE` は 7 のまま。
  - **REST 4 本**: `GET /us-stocks`・`GET /us-stocks/screen`・`GET /us-stocks/{symbol}`（マスタ未取得 404／valuation 未焼成 200＋null）・`GET /us-quotes/{symbol}`（[api.md](api.md)）。frontend は `/us-stocks` スクリーナー＋ `/us-stocks/[symbol]` 詳細（$ 表示・GICS フィルタ・ローソク足）。
  - **YoY は取れる実値だけ採る**（捏造しない＝[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け)）。`.info` はスナップショット 1 点で前期 FY 値を持たないため `growth_yoy` 純関数の素にできない。`.info` 自身が提供する率を**実値として中継**＝`revenue_growth_yoy`←`.info.revenueGrowth`（売上 YoY）・`profit_growth_yoy`←`.info.earningsGrowth`（純利益 YoY）。**`op_growth_yoy`/`eps_growth_yoy` は素が無いため None**。
  - **`operating_profit` は近似**（`.info` に直接の項目が無いため `operatingMargins × totalRevenue` で近似・adapter docstring に明記＝[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け)）。
- **理由**: スクリーナー（提示）は通貨非依存（PER/PBR/利回り/各 margin/YoY は比率・市場内ランクも市場内に閉じる）で、$ 単独で完結する。ここに FX 換算や保有波及を持ち込まなければ、JPY 単一前提の資産評価コアを一切壊さずに「米株を AI と相談できる」価値を先に出せる。データ源を yfinance に絞るのは、(B-1) の提示用途には OHLCV ＋ `.info` ＋ NASDAQ Trader directory で足り、有償 API のコスト/レート制限を負う理由がないため。GICS を文字列保持に留めるのも同じ＝厳密分類は提示の精度に寄与せず、別ソースの維持コストに見合わない。
- **代替案**:
  - **IEX Cloud / Polygon 等の有償ファンダ API を主にする** → (B-1) の提示用途には過剰。コスト・レート制限・キー管理を (B-1) で負う理由がない。将来 (B-2) 以降で精度/銘柄数が要るときフォールバック連鎖（[ADR-010](#adr-010-データソースはアダプタ越しにする)）へ足せばよい。却下。
  - **厳密な GICS 分類ソース（公式 GICS コード）を引く** → 有償/ライセンス制約があり、提示精度への寄与も薄い。`.info.sector` の英語ラベル（GICS 相当 11 分類）で業種絞り込み・業種内ランクは十分機能する。却下（文字列保持を採用）。
  - **(B) を分割せず一括（FX/保有波及まで）実装** → 重い通貨波及に軽い提示が引きずられる。却下（(B-1)/(B-2) 分割が本 ADR の主旨）。
  - **米株を `stocks`/`daily_quotes` に混ぜる** → 通貨/業種分類/財務ソースが食い違い、市場内ランクが無意味になる（[ADR-031](#adr-031-株式スクリーナー夜間-valuation_snapshots-読み取り時ランク市場ごとに分離) 市場分離）。却下（別テーブル）。
- **TODO（落とさない・明示管理）**:
  - **(B-2)＝FX 換算/保有波及**: → **[ADR-057](#adr-057-phase-7b-2fx-基盤米株保有管理資産概要合算を最小スコープで実装する) で実装済み（2026-06-11）**。採用した最小スコープは「`FxAdapter`・`fx_rates`・`us_transactions`/`us_holdings`・`asset_snapshots.us_stock_value`」で、holdings/cash/portfolio metrics の通貨波及は今回含めず将来課題に明示。currency 列の us_stocks 等への追加も見送り。
  - **repo/handler/service の日米 DRY 共通化**: (B-1) は日本株を無改変に保つため**重複を許容**した（`us_stocks.py` ルータ・`screen_us_stocks` 等が日本株版のミラー）。共通化は (B-2) 以降で安定してから詰める。
  - **米株版 25 指標フル充足**: [ADR-048](#adr-048-銘柄バリュエーションroeperpbr基準を-tool事実参照知識カードで持たせる) の横断 TODO（ROA/ROIC 等）を米株でも。`.info` に無い指標は別ソースが要る。
  - **op/eps の YoY を活かす**: `op_growth_yoy`/`eps_growth_yoy` を None でなく実値にするには、財務履歴源（前期 FY 値）を追加して `growth_yoy` 純関数の素を揃える必要がある。`.info` のスナップショット中継では出せない。
- **段階**: **実装済み（2026-06-09）**。`adapters/us_equity.py`・schema `0017_us_equity`・夜間 4 ジョブ・`routers/us_stocks.py`・AI Tool 2 つ・frontend `/us-stocks` ＋ `/us-stocks/[symbol]`。pytest green。**(B-2) FX/保有波及は [ADR-057](#adr-057-phase-7b-2fx-基盤米株保有管理資産概要合算を最小スコープで実装する) で解消済み（2026-06-11）**。
- **実装メモ（2026-06-24・`fetch_us_quotes` のバルク化）**: 当初 `fetch_us_quotes` は本 ADR が「`yf.download` バッチ一括」と明記していたのに **per-symbol 取得（1 銘柄ずつ ＋ 1 秒スロットル）に落ちていた（ドリフト）**。全 11,003 銘柄で約 3 時間かかり夜間枠で完走できず途中停止、全銘柄共通カーソルが「取れた最大 date」へ前進するため毎晩**先頭から**処理して後半に到達せず、NIGHTLY 順で後段の `fetch_us_fundamentals`/`calc_us_valuation` まで届かず **`us_valuation_snapshots` が空＝米株スクリーナーが死ぬ**実害が出た。当初設計どおり **`adapter.fetch_quotes_bulk`（`yf.download` 複数シンボル一括・`group_by='ticker'` で分解し既存 `_rows_from_df` を再利用）へ収束**させ、HTTP 回数とスロットル待ちを symbol 数→バッチ数（`us_quotes_batch_size`）に削減した。バッチ全滅は raise（ADR-018 対称）・部分欠損 symbol は dict から落として呼び出し側が計数。あわせて NASDAQ Trader パーサに**純数字 symbol 拒否ガード**（過去に列ズレで混入した日本株コード `18330`/`18350` の再発防止）と `repo.delete_us_stock`（既存ゴミの一回掃除）を追加した。**directory から消えた銘柄の差分同期（上場廃止掃除）は別 TODO**（`sync_us_universe` の UPSERT は削除を伴わない・is_active/reconcile は別設計判断）。
- **関連**: [ADR-039](#adr-039-phase-7-を-a-sector-lead-lag-先行b-米株拡張に分割し-a-の業種-etf-は-indexadapter-に-yahoo-ソースを足して流用する)（Phase 7(B)・`UsEquityAdapter` 新設の明言）・[ADR-031](#adr-031-株式スクリーナー夜間-valuation_snapshots-読み取り時ランク市場ごとに分離)（市場分離・読み取り時ランク・通貨ポリシー）・[ADR-048](#adr-048-銘柄バリュエーションroeperpbr基準を-tool事実参照知識カードで持たせる)（valuation 列・Tool 契約 market/currency・verdict なし）・[ADR-010](#adr-010-データソースはアダプタ越しにする)（アダプタ越し・フォールバック連鎖）・[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け)/[ADR-016](#adr-016-手法はコードで実装する手法db-は索引でありコードの代替ではない)（事実は Python・捏造しない）・[ADR-033](#adr-033-銘柄ごとの調査-cadence-は-interval_days-夜あたり天井で律速する)（財務ローテ巡回）・[ADR-009](#adr-009-自動売買はしない提示に徹する)（提示専用）・[data-model.md](data-model.md)・[api.md](api.md)・[roadmap.md Phase 7](roadmap.md)。

---

## ADR-056: EDINET を JP の事業説明テキスト源にする（有報「事業の内容」を要約して company_descriptions に持つ）

- **状況/問題**: [ADR-050](#adr-050-銘柄テーマは実在テキストに-grounded-な全ユニバース事前タグで持つ改訂名前推測を禁じ-edinetlongbusinesssummary-を信号源にする) 改訂で「全ユニバースを実在テキストに grounded で事前タグ付け」を決めたが、**JP の未調査銘柄には銘柄を語る権威ある実在テキストが無い**。J-Quants（[ADR-008](#adr-008-j-quants-は-v2x-api-key-を使うv1-は使わない)）は価格・財務・銘柄マスタ（社名/業種）を返すが、事業内容の説明文は提供しない。米株は `.info.longBusinessSummary`（[ADR-055](#adr-055-米株スクリーナーphase-7b-1は-yfinance-一本gics-は-yahoo-infosector-の文字列保持提示専用で-jpy-資産評価コアに触れない)）で賄えるが、JP に等価の信号源が欠けている。
- **検討して却下した案**:
  - **社名＋セクターから推測** → [ADR-050](#adr-050-銘柄テーマは実在テキストに-grounded-な全ユニバース事前タグで持つ改訂名前推測を禁じ-edinetlongbusinesssummary-を信号源にする) で却下済み（ハルシネーション）。
  - **企業 HP/Wikipedia をスクレイプ** → 出典が不安定・ノイズが多く、権威性に欠ける。却下。
  - **dexter-jp 型の EDINET ベース外部 SaaS に判断を委ねる** → [ADR-048](#adr-048-銘柄バリュエーションroeperpbr基準を-tool事実参照知識カードで持たせる) が「バリュエーション判断の外部委譲」を却下済み。本 ADR はその轍を踏まない（下記・非衝突）。
- **決定**:
  - **EDINET API v2 を「事業の内容」テキスト専用の追加ソースにする**。価格・財務・銘柄マスタは [ADR-008](#adr-008-j-quants-は-v2x-api-key-を使うv1-は使わない) どおり J-Quants を継続し、**EDINET は additive（置換でない）**。
  - **`EdinetAdapter`（[ADR-010](#adr-010-データソースはアダプタ越しにする) 境界）** を新設＝銘柄コード→最新有報 docID を書類一覧 API（`docTypeCode=120`・`secCode` 絞り）で解決→書類取得 API（`type` で CSV/XBRL）→ `DescriptionOfBusinessTextBlock`（事業の内容）を抽出。adapter は DB に触らない（外部 IO のみ）。
  - **取得した「事業の内容」は要約して compact 化**してから保存する（[ADR-020](#adr-020-個別銘柄ドシエ定性ファンダ調査-1銘柄1レポートを更新し続ける) の「取得→要約→本文捨てる」と同イディオム）。保存先は市場横断の `company_descriptions`（[data-model.md](data-model.md)・`market`/`code`/`source='edinet'`/`description_text`/`disclosed_date`/`doc_id`/`fetched_at`）。米株の `longBusinessSummary`（`source='yfinance'`）も同テーブルに同居する。
  - **API キーは backend `.env`（`EDINET_API_KEY`・無料登録）**。秘密は backend のみ（[ADR-005](#adr-005-db-に触るのは-fastapi-だけnextjs-は-ui-専用)）。
  - **cadence**＝バックフィル一括（全 JP 銘柄の最新有報を巡回取得）＋差分（新規有報＝年次・低 churn を `disclosed_date` で検知）。書類一覧 API は提出日でクロールするため、初回は実質ミニ・バックフィル巡回になる（[ADR-018](#adr-018-無人運用の障害時方針失敗を黙って放置しない) で部分失敗を握る）。
- **理由**: 有報「事業の内容」は金融庁提出の**権威ある実在テキスト**で、grounded テーマ付け（[ADR-050](#adr-050-銘柄テーマは実在テキストに-grounded-な全ユニバース事前タグで持つ改訂名前推測を禁じ-edinetlongbusinesssummary-を信号源にする)）の JP 信号源として最適。価格・財務を J-Quants に残し EDINET をテキスト専用に限定することで、データ源の責務が混ざらない（[ADR-010](#adr-010-データソースはアダプタ越しにする)）。
- **ADR-048 との非衝突**: [ADR-048](#adr-048-銘柄バリュエーションroeperpbr基準を-tool事実参照知識カードで持たせる) が却下したのは「バリュエーション**判断**の外部 SaaS 委譲」。本 ADR は EDINET を**生テキスト源**として使うだけで、数値判断は引き続き quant 純関数が持つ（[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け)）。判断の委譲ではないため衝突しない。
- **段階**: **実装済み（2026-06-11）**。[ADR-050](#adr-050-銘柄テーマは実在テキストに-grounded-な全ユニバース事前タグで持つ改訂名前推測を禁じ-edinetlongbusinesssummary-を信号源にする) 段階 C（JP 全ユニバース）として実装＝`EdinetAdapter`（書類一覧 type=2／取得 type=5＝CSV ZIP から `DescriptionOfBusinessTextBlock`）／`advisor/edinet_summary`（要約）／提出日クロール core `batch/jobs/fetch_edinet_descriptions`（夜間差分・カーソル `fetch_meta('edinet:crawl')`・dossier 優先 2 段ガード）／`app.scripts.backfill_edinet`（15ヶ月窓・中断再開可）／`POST /edinet/run-differential`＋`/settings`。pytest green。`secCode`↔`stocks.code`（5桁）直接一致・migration 不要（`company_descriptions` の既存列を流用）。詳細は [ADR-050](#adr-050-銘柄テーマは実在テキストに-grounded-な全ユニバース事前タグで持つ改訂名前推測を禁じ-edinetlongbusinesssummary-を信号源にする) 「実装メモ（段階C・2026-06-11）」。[data-model.md](data-model.md) に `company_descriptions` を同期。
- **関連**: [ADR-050](#adr-050-銘柄テーマは実在テキストに-grounded-な全ユニバース事前タグで持つ改訂名前推測を禁じ-edinetlongbusinesssummary-を信号源にする)（信号源として消費）・[ADR-008](#adr-008-j-quants-は-v2x-api-key-を使うv1-は使わない)（JP=J-Quants を価格/財務で継続・EDINET は追加）・[ADR-010](#adr-010-データソースはアダプタ越しにする)（アダプタ越し）・[ADR-048](#adr-048-銘柄バリュエーションroeperpbr基準を-tool事実参照知識カードで持たせる)（非衝突）・[ADR-020](#adr-020-個別銘柄ドシエ定性ファンダ調査-1銘柄1レポートを更新し続ける)（要約イディオム）・[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け)（数値は quant）・[ADR-005](#adr-005-db-に触るのは-fastapi-だけnextjs-は-ui-専用)（秘密は backend）。

---

## ADR-057: Phase 7(B-2)＝FX 基盤・米株保有管理・資産概要合算を最小スコープで実装する

- **状況/問題**: [ADR-055](#adr-055-米株スクリーナーphase-7b-1は-yfinance-一本gics-は-yahoo-infosector-の文字列保持提示専用で-jpy-資産評価コアに触れない) で (B-2) に繰り延べた「FX 換算・米株保有管理・JPY 資産概要への合算」を実装する。(B-1) が提示専用＝JPY 資産評価コアに一切触れない設計で先行したため、今回はじめて USD 資産を JPY 資産概要と結ぶ。一方、JPY 資産評価コアそのもの（`holdings`/`cash`/`portfolio metrics`/`/optimize`）を大規模改修するのではなく、**資産概要レイヤ（`asset_snapshots` と `/asset-overview`）でのみ合算する**最小スコープに絞る。
- **決定**:
  - **スコープを 3 要素に限定する**: (a) FX 基盤（`FxAdapter`・`fx_rates` テーブル・夜間 `fetch_fx_rates`）、(b) 米株保有管理（`us_transactions`・`us_holdings`・`services/us_holdings.py`）、(c) 資産概要合算（`asset_snapshots.us_stock_value` 追加・`/asset-overview` に米株スライス）。**`holdings`/`cash`/`portfolio metrics`/`/optimize` への通貨波及は今回含めない**（[ADR-031](#adr-031-株式スクリーナー夜間-valuation_snapshots-読み取り時ランク市場ごとに分離) の市場分離維持）。
  - **FX 取得源は yfinance `JPY=X` 日足終値**（`FxAdapter`・`adapters/fx.py`・[ADR-055](#adr-055-米株スクリーナーphase-7b-1は-yfinance-一本gics-は-yahoo-infosector-の文字列保持提示専用で-jpy-資産評価コアに触れない) の `UsEquityAdapter` と同型のフォールバック連鎖ファサード・[ADR-010](#adr-010-データソースはアダプタ越しにする)）。テーブル `fx_rates(date, pair, rate)`（`rate` は JPY/USD＝1 USD あたりの円）。config に `fx_source`/`fx_min_interval_seconds`/`fx_http_timeout_seconds`。依存追加なし（yfinance は既出）。
  - **米株保有の入力は取引ベース**（[ADR-019](#adr-019-保有は取引から導出する-holdings-は-transactions-の射影) と同型）。`us_transactions`（`id`/`symbol`→`us_stocks` FK/`side`/`shares`/`price`〔USD〕/`fee`/`traded_at`/`fx_rate`〔約定時 USDJPY〕/`note`）を一次データとし、`us_holdings`（`symbol` UNIQUE/`shares`/`avg_cost`〔USD〕/`avg_cost_jpy`〔JPY 固定原価〕）を導出する。**[ADR-001](#adr-001-単一ユーザー前提で作る) の単一ユーザー前提ゆえ `portfolio_id` を持たない**（JPY 側の `holdings` が `portfolio_id` を持つのとは違い、グローバル保有とする）。再導出は `services/us_holdings.py` の `recalc_us_holdings(conn, symbol)`（symbol 単位・共有純関数 `recompute_positions` を price と price_jpy=price×fx_rate の 2 引数で呼び、USD と JPY 両原価を出す）。mutation 後に atomic 再計算（[ADR-019](#adr-019-保有は取引から導出する-holdings-は-transactions-の射影)）。
  - **含み損益の為替処理を「約定時固定 + 現レート評価」にする**。取得原価は約定時 USDJPY で JPY に固定（`avg_cost_jpy`）。評価額は現レート×最新 close で算出。**これにより為替損益が含み損益に乗る**（為替差損益を区別したい場合は将来の拡張）。`value_us_holdings(holdings_rows, latest_closes_usd, fx_rate)` が JPY 換算を行う通貨非依存の純関数（[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け)/[ADR-016](#adr-016-手法はコードで実装する手法db-は索引でありコードの代替ではない)）。
  - **資産概要合算は `asset_snapshots.us_stock_value` 列追加**（`fund_value` と同型・[ADR-054](#adr-054-投資信託の保有管理を株と同型の取引ベース導出で持つ) が先例）。`/asset-overview` の `total_value`/`pnl` に合算・`allocation` に「米国株」スライスを追加。`snapshot_assets` 夜間ジョブが当夜 FX×最新 close で焼く。
  - **夜間ジョブ `fetch_fx_rates`** を `snapshot_assets` の直前に配置する（当夜の FX を当夜の `snapshot_assets` に確実に反映するため）。`fetch_meta['fx:USDJPY']` カーソルで差分取得。migration `0019_us_holdings_fx`。
  - **API**:
    - `GET /us-holdings` → 保有中の米株一覧（USD/JPY 両評価・含み損益）
    - `GET /us-transactions` → 取引履歴
    - `POST /us-transactions` → 取引記録（body の `fx_rate` → 約定日 FX → なければ 400 の順で解決）
    - `PUT /us-transactions/{id}` → 取引編集
    - `DELETE /us-transactions/{id}` → 取引削除
    - `GET /asset-overview` に `us_stock_value` を追加（既存 `fund_value` と並ぶ）
  - **AI Tool `get_us_holdings`**（`min_phase=7`）: 米株保有を JPY 評価で返す（日米横断のバランス相談を AI が一体で語れるようにするため）。
- **理由**:
  - JPY 資産評価コア（`holdings`/`cash`/`/optimize`）への通貨波及は規模が大きく既存機能のリスクが高い。一方「米株がいくら含み益/損か」「総資産に占める米株割合は」という実用的な問いは、資産概要レイヤ（スナップショット合算）だけで答えられる。最小スコープに絞れば、JPY 単一前提コアを壊さず米株保有の価値を先に出せる。
  - 取引ベース保有管理（[ADR-019](#adr-019-保有は取引から導出する-holdings-は-transactions-の射影)）を踏襲することで、買値・約定日・FX レートが全取引に記録され、将来の損益計算・税務用途に耐える基盤になる。
  - `avg_cost_jpy`（約定時固定）を持つことで、将来「為替損益と株価損益を分離表示」する拡張の余地を確保する（現時点では合算して含み損益に出す）。
- **代替案**:
  - **`holdings`/`cash` に currency 列を足して JPY 資産評価コアを全面的に多通貨対応にする** → `portfolio metrics`（相関・最適化・バックテスト）まで全面改修が要る。リスクが大きく、「米株保有が資産概要に出る」という今回の目的に対して過剰。却下（将来の Phase で検討）。
  - **USD 保有を `external_assets` に手入力する（今まで通り）** → 取引履歴が残らず含み損益が手動更新頼り。FX も自分で計算する必要がある。却下（本 ADR の目的を達成しない）。
  - **FX 取得源に別サービス（OpenExchangeRates 等）を使う** → yfinance は既に依存済みであり、`JPY=X` が USDJPY 日足を提供している。追加依存を増やす理由がない。却下。
- **段階**: **実装済み（2026-06-11）**。`adapters/fx.py`・`services/us_holdings.py`・schema `0019_us_holdings_fx`（`fx_rates`・`us_transactions`・`us_holdings`・`asset_snapshots.us_stock_value`）・夜間ジョブ `fetch_fx_rates`・`routers/us_holdings.py`・AI Tool `get_us_holdings`・`/asset-overview` 合算。pytest green。
- **関連**: [ADR-055](#adr-055-米株スクリーナーphase-7b-1は-yfinance-一本gics-は-yahoo-infosector-の文字列保持提示専用で-jpy-資産評価コアに触れない)（(B-1)/(B-2) 分割・FX/保有波及を本 ADR で解消）・[ADR-039](#adr-039-phase-7-を-a-sector-lead-lag-先行b-米株拡張に分割し-a-の業種-etf-は-indexadapter-に-yahoo-ソースを足して流用する)（Phase 7(B) 繰り延べの解消）・[ADR-019](#adr-019-保有は取引から導出する-holdings-は-transactions-の射影)（取引ベース保有導出・atomic 再計算）・[ADR-054](#adr-054-投資信託の保有管理を株と同型の取引ベース導出で持つ)（fund_value の先例＝asset_snapshots への追加カラム）・[ADR-031](#adr-031-株式スクリーナー夜間-valuation_snapshots-読み取り時ランク市場ごとに分離)（市場分離の維持）・[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け)/[ADR-016](#adr-016-手法はコードで実装する手法db-は索引でありコードの代替ではない)（事実は Python）・[ADR-010](#adr-010-データソースはアダプタ越しにする)（FxAdapter）・[ADR-001](#adr-001-単一ユーザー前提で作る)（portfolio_id なし）・[data-model.md](data-model.md)・[api.md](api.md)・[roadmap.md Phase 7](roadmap.md)。

## ADR-058: LLM プロバイダ・面別 provider/model 設定を env から DB＋WebUI（/settings）へ移管する

> **注記（[ADR-073](#adr-073-codex-接続の撤去adr-032-を-superseded)）**: 本 ADR の「LLM 設定を DB+WebUI へ移管」という決定は現行のまま。ただし codex に関する記述（`provider_id=0` センチネル・残す env に `CODEX_*`・実装ファイルの `codex_engine.py`）は ADR-068 で撤去済み。provider は OpenAI 互換のみ。

- **状況/問題**: LLM 設定は `config.py` の pydantic `BaseSettings`（env 由来・起動時固定 singleton）に閉じ込められ、(1) `llm.py` の `_client = AsyncOpenAI(...)` がモジュールロード時の単一 singleton で base_url/api_key を実行時に差し替えられない、(2) model は `settings.llm_model` の全面共通 1 値で面（chat/nightly/dossier）ごとに変えられない、(3) provider は `LLM_PROVIDER_*` で openai/codex を面別選択できるだけで、複数の OpenAI 互換 provider（OpenAI 直 / Claude / ローカル / Sakana）を同時登録して面別に割り当てられない、という制約があった。「この面は GPT-5、この面は Claude Opus 4.8、この面は Sakana fugu」のように面ごとに provider と model を**同時に**割り当てたい。
- **決定**:
  - **正本を DB に移す**。新テーブル `llm_providers`（鍵あり provider のレジストリ・複数行）と `llm_face_config`（面→{provider_id, model} の 4 行運用）を追加（`0022_llm_providers`）。provider/api_key/base_url/model と面別割当は `/settings` の WebUI から編集する。env の `LLM_API_KEY`/`LLM_BASE_URL`/`LLM_MODEL`/`LLM_PROVIDER_CHAT|NIGHTLY|DOSSIER` は**廃止**しデッドコードを残さない（残すのは接続パラメータ `LLM_TIMEOUT_SECONDS`/`LLM_MAX_RETRIES`/`LLM_RETRY_BASE_SECONDS`、コストガード `LLM_COST_*`、codex プロセス設定 `CODEX_*`、embedding `EMBEDDING_*`）。
  - **OpenAI 互換 1 本で全 provider を吸収する**（[ADR-012](#adr-012-llm-はアダプタで抽象化しenv-で-openrouter--ollama-を差し替える) の踏襲）。openai/chatgpt・claude・localLLM・sakanaai は全部 `{base_url, api_key, model}` で扱い、Claude も OpenRouter か Anthropic の OpenAI 互換エンドポイント経由とする（**Anthropic ネイティブ SDK は入れない**）。**真に特殊なのは codex だけ**。
  - **codex は鍵なし組み込み 1 固定**。`llm_providers` に行を持たず、`llm_face_config.provider_id=0` をセンチネルとして `services/llm_config.resolve_face` が codex 経路へ解決する。UI で追加/削除しない（面のセレクトで「codex」を選ぶ）。codex のプロセス設定（`codex_bin`/`sandbox`/`mcp_url`/`timeout`/`reasoning_effort`）は env 据え置きで、per-face で変えるのは **model だけ**（空なら `settings.codex_model`）。
  - **4 面を独立設定**: chat / nightly / dossier / **tagger**。従来 `source="tagger"`（`theme_tagger`/`news_polarity`）は `provider_for` のマップ外で openai+llm_model 固定に落ちていた不整合を、正式な 4 面目にして解消する。embedding は別系統で本機能の対象外（env 据え置き）。
  - **解決はリクエスト毎に DB を引く**（`engine.resolve_face(source)` が毎回 connect。face 行は 4 行で軽い・`_check_cost_guard` と同じ前例）。これで UI で provider/model を変えたら次の呼び出しから反映される（キャッシュ無効化機構を持たない）。OpenAI クライアントは `(base_url, api_key)` をキーに `llm.get_client` でキャッシュ生成し、鍵更新で新キー＝自然に切り替わる。
  - **API キーは SQLite に平文保存**（[ADR-001](#adr-001-単一ユーザー前提で作る) の単一ユーザー・認証なし・LAN 内が前提）。GET では必ずマスク（末尾 4 桁）し、更新は **write-only**（空文字も None も据え置き＝鍵削除は provider 削除→再作成で代替）。**v1 は平文だが、将来は暗号化保存したい**（env のアプリ鍵で暗号化＝移行余地を残す）。
  - **provider 削除が面に使用中なら 409 で拒否**（FK は張らずアプリ層で守る・先に面割当を外させる）。
  - **未設定面は [ADR-018](#adr-018-llm-障害時はフォールバックで縮退し夜間は通知して当日をスキップする) 準拠**。`resolve_face` は `FaceNotConfiguredError` を投げ、chat（router）は 503 の明示エラー、nightly/dossier は通知付き skip（runner 集約）、**tagger は沈黙 skip**（enrichment 扱い＝embed_news 同型）。codex は最初から使えるが、鍵あり provider は UI 登録まで該当面が動かない（シードしない＝初回は手動登録）。
- **理由**:
  - 複数 provider の同時登録と面別割当は単一 base_url/model の env 構成では原理的に不可能で、DB 化が要る。`policy`（[ADR-013](#adr-013-投資方針-policy-は単一を育てる複数ペルソナや版管理は作らない)）が既に「DB 保存して UI 編集」の前例で、同じ作法に乗せられる。
  - OpenAI 互換 1 本（[ADR-012](#adr-012-llm-はアダプタで抽象化しenv-で-openrouter--ollama-を差し替える)）を貫けば Claude も含め provider 分岐が要らず、特殊経路を codex だけに閉じ込められる（実装・テスト・障害処理の最小化）。
  - 平文保存は [ADR-001](#adr-001-単一ユーザー前提で作る) の脅威モデル（単一ユーザー・認証なし・LAN 内・外部公開しない）に整合する。暗号化は将来余地として明記し、過剰設計を避ける。
- **代替案**:
  - **env のまま面別 model だけ足す**（`LLM_MODEL_CHAT` 等）→ 複数 provider 同時登録ができず、WebUI 編集の要件も満たさない。却下。
  - **Anthropic ネイティブ SDK を別経路で持つ** → provider 抽象に 2 つ目の特殊経路（tool 変換・障害処理・コスト計上・テスト）が増える。Claude は OpenAI 互換層で十分動くため却下（[ADR-012](#adr-012-llm-はアダプタで抽象化しenv-で-openrouter--ollama-を差し替える)）。
  - **env を残し DB を上書きレイヤにする** → 「この値はどっち由来か」が曖昧になる。DB 一本（シードなし）に倒した。
  - **API キーを暗号化保存** → 単一ユーザー LAN 前提では鍵管理（env の SECRET）が過剰。v1 は平文・将来余地として記録（この ADR）。
- **既知の限界**: コストガード（[ADR-028](#adr-028-llm-月額コストガードレールを-warn-既定-block-でかける)）の `usage.cost` は OpenRouter 拡張依存で、**OpenRouter 以外の provider は cost を返さず 0 計上＝当月累計が過小評価されガードが空洞化する**。`llm_usage.model` に面別の実 model を記録する（将来 model 別単価表で概算する余地）。
- **段階**: **実装済み（2026-06-24・backend＋frontend）**。`db/schema.py`（`llm_providers`/`llm_face_config`）・`0022_llm_providers`・`db/repo/llm_config.py`・`services/llm_config.py`（`resolve_face`/`describe_faces`/`FaceNotConfiguredError`）・`advisor/engine.py`（face 解決ディスパッチ）・`advisor/llm.py`（`get_client` キャッシュ＋per-face model）・`advisor/codex_engine.py`（per-face model）・`routers/llm_config.py`（`/llm/providers`・`/llm/faces`・疎通テスト）・frontend `lib/api/llm-config.ts`＋`components/settings/LlmSettings.tsx`（/settings）。pytest green（resolve_face/router/dispatch を含む）。env から `LLM_API_KEY`/`LLM_BASE_URL`/`LLM_MODEL`/`LLM_PROVIDER_*` と `config.provider_for` を撤去。
- **関連**: [ADR-012](#adr-012-llm-はアダプタで抽象化しenv-で-openrouter--ollama-を差し替える)（OpenAI 互換 1 本・base_url/model/api_key 差替）・[ADR-032](#adr-032-codex-接続は-mcpcodex-app-serverapicodex-を面別切替自動フォールバックなし)（codex 面別切替・`provider_for` の env 機構を本 ADR が DB＋WebUI へ置換）・[ADR-013](#adr-013-投資方針-policy-は単一を育てる複数ペルソナや版管理は作らない)（DB 保存して UI 編集の前例＝policy）・[ADR-005](#adr-005-db-に触れる-os-プロセスは-fastapi-だけにする)（DB は FastAPI だけ）・[ADR-001](#adr-001-単一ユーザー前提で作る)（平文・LAN 内前提）・[ADR-018](#adr-018-llm-障害時はフォールバックで縮退し夜間は通知して当日をスキップする)（未設定面のフォールバック）・[ADR-028](#adr-028-llm-月額コストガードレールを-warn-既定-block-でかける)（コストガードの限界）・[data-model.md](data-model.md)・[api.md](api.md)・[architecture.md](architecture.md)。

## ADR-059: 面別 reasoning_effort ＋ codex 状態確認 ＋ embedding 接続の DB+WebUI 化（ADR-058 拡張）

> **注記（[ADR-073](#adr-073-codex-接続の撤去adr-032-を-superseded)）**: 本 ADR のうち「codex 状態確認」（`POST /llm/codex/test`）と codex 面の reasoning env フォールバックは ADR-068 で撤去済み。面別 reasoning_effort（openai）と embedding 接続の DB+WebUI 化は現行。reasoning の値域は minimal/low/medium/high（xhigh は codex 固有だったため撤去）。

- **状況/問題**: [ADR-058](#adr-058-llm-プロバイダ面別-providermodel-設定を-env-から-dbwebuisettings-へ移管する) で v1 として先送りした 2 点（面別 reasoning_effort・embedding の WebUI 化）と、ユーザー要望（codex が使用可能かを `/settings` で確認したい）を実装する。reasoning_effort は当時「面の粒度は provider+model のみ」とし、embedding は env 据え置き、codex は鍵なし組み込みで provider 一覧に出ない、という状態だった。
- **決定**:
  - **reasoning_effort を面別（`llm_face_config.reasoning_effort` 列・0023）にする**。openai 経路は `chat.completions.create(reasoning_effort=...)`、codex 経路は thread/start `config.model_reasoning_effort` に渡す（openai/codex 対称）。`resolve_face` が解決し、**codex 面は空なら env `codex_reasoning_effort` にフォールバック**（`codex_model` と同じ作法）・openai 面は空なら**送らない**。UI は固定ドロップダウン（`（既定）`(空)/minimal/low/medium/high/xhigh）。
  - **非対応 model に reasoning を設定したら送り、provider の 400 はそのまま表面化**（chat=502・夜=通知付き skip）。**自動縮退（reasoning を外して再試行）はしない**＝誤設定を隠さない。既定（空）は何も送らず従来挙動。
  - **codex は状態カード（疎通テストのみ）**。`POST /llm/codex/test` が最小の単発生成（`generate_once`・ChatGPT サブスク＝USD コスト無し）を試し使用可否を返す。model/reasoning は面別で設定するため codex カードに設定欄は持たない。ボタン押下時のみ実行（ページ表示で codex app-server を起こさない）。
  - **embedding 接続を DB（`embedding_config`・単一行・0023）＋`/settings` カードへ移管**。`base_url`/`api_key`/`model`/`dim` を画面で編集（api_key は平文・GET でマスク・write-only）。chat provider とは独立（埋め込みは別エンドポイント `/v1/embeddings`・別 model・別キーが普通）。`adapters/embedding.py` は `resolve_embedding_config()`（DB）読みに変更し、3 キー欠落なら静かに機能オフ（ADR-006/045）。env の `EMBEDDING_BASE_URL`/`API_KEY`/`MODEL`/`DIM` は撤去（`EMBEDDING_TIMEOUT_SECONDS` は接続パラメータとして据え置き）。
- **理由**:
  - reasoning は面ごとに変えたい（チャットは high・タグ付けは minimal 等）。1 列で openai/codex 両対応でき、codex も thread/start で毎回渡せるため per-face が自然。env はフォールバックに降ろせば既存運用と両立。
  - codex は鍵が無く「設定済みか」が provider 一覧で見えない。実ターン 1 発の疎通が「使用できる状態か」の唯一確実な信号（login の有効性まで確認できる）。
  - embedding を env に残すと chat だけ DB・embedding だけ env と分裂する。DB+WebUI に揃えると設定の真実が一箇所に集まる（ADR-058 と同じ作法・policy/embedding_config の単一行運用）。
- **代替案**:
  - **codex reasoning を codex グローバル 1 値にする** → openai=面別・codex=グローバルの非対称＋codex 用の保存先が増える。面別 1 列に統一して却下。
  - **400 を自動縮退（reasoning を外して再試行）** → 「設定したのに効いてない」が静かに起き、リトライ経路も増える。誤設定を表面化する方を採用。
  - **embedding を chat provider のエントリ参照で持つ** → 埋め込みキーが chat と違う場合に困り、provider 削除で embedding が壊れる。独立の単一行に倒した。
- **段階**: **実装済み（2026-06-24・backend＋frontend）**。`0023_llm_reasoning_embedding`（`llm_face_config.reasoning_effort`・`embedding_config`）・`services/llm_config`（`ResolvedFace.reasoning_effort`・`resolve_embedding_config`）・`advisor/llm.py`/`codex_engine.py`/`engine.py`（reasoning 配線）・`adapters/embedding.py`（DB 解決・`embedding_model()`）・`routers/llm_config.py`（face reasoning・`/llm/embedding`・`/llm/codex/test`）・frontend（reasoning ドロップダウン・codex 状態カード・embedding カード）。env から `EMBEDDING_BASE_URL`/`API_KEY`/`MODEL`/`DIM` を撤去。pytest green。
- **関連**: [ADR-058](#adr-058-llm-プロバイダ面別-providermodel-設定を-env-から-dbwebuisettings-へ移管する)（基盤＝本 ADR の前提）・[ADR-045](#adr-045-ニュース意味検索embedding-は段階導入vec0-は後回しまずは-blobsqlite-で素朴に)（embedding 意味検索）・[ADR-032](#adr-032-codex-接続は-mcpcodex-app-serverapicodex-を面別切替自動フォールバックなし)（codex 接続）・[ADR-006](#adr-006-重い計算は別pc-ラズパイは推論のみ)（未設定は静かに機能オフ）・[ADR-018](#adr-018-llm-障害時はフォールバックで縮退し夜間は通知して当日をスキップする)（未設定面）・[data-model.md](data-model.md)・[api.md](api.md)。

## ADR-060: dev の SQLite は named volume に載せる（macOS Docker Desktop の bind mount では WAL/mmap が壊れるため）・prod は bind mount 維持

- **状況/問題**: 2026-06-22、`data/assetvane.db` が**構造破損**した。`PRAGMA integrity_check` が「2nd reference to page（複数 b-tree がページを二重参照＝フリーリスト破損）」「Rowid out of order」を多数報告し、`us_daily_quotes` は `count(*)` すら `database disk image is malformed` で読めず、`us_valuation_snapshots` 実値 0 ＝ 米株スクリーナーが死んだ。原因の見立ては、6/15〜17 の米株全 10919 銘柄バックフィル（数時間の重い書き込み）が、**macOS Docker Desktop の bind mount（gRPC-FUSE/virtiofs）上の SQLite WAL/mmap** の相性問題でページ破損を誘発したこと（`batch.lock` 残留・DB mtime が 6/17 で停止していたのと整合）。`fcntl.flock` 方式の `batch.lock`（`batch/lock.py`・FD 寿命に紐付く）は犯人ではない。[ADR-002](#adr-002-データベースは-sqlitewal-モード) の WAL も [ADR-021](#adr-021-開発本番ともコンテナdocker-composeで動かす) の Compose 運用も前提のまま、**dev の DB 置き場だけが地雷**だった（[ADR-021](#adr-021-開発本番ともコンテナdocker-composeで動かす) の文面は「named volume（`data/`）」と曖昧で、実際の `compose.yaml` は bind mount `./data:/data` ＝ドリフトしていた）。
- **決定**:
  - **dev（`compose.yaml`）の DB を bind mount `./data:/data` → named volume `assetvane-db:/data` に変更する**。named volume は Docker VM 内の ext4（ネイティブ fs）に載るため FUSE 層を挟まず、SQLite の WAL/mmap が前提とするファイルロック・mmap セマンティクスが正しく満たされる。`docker compose down` では消えず、消すときは明示的に `docker compose down -v` か `docker volume rm`。
  - **prod（`compose.prod.yaml`・ラズパイ）は bind mount `./data:/data` を維持する**。ネイティブ Linux で FUSE 問題が無く、かつ [ADR-017](#adr-017-sqlite-を定期バックアップする) のバックアップがホストから素ファイルとして `/data/backups` を読める必要があるため。
  - **named volume はホストから素ファイルで見えない**ので、バックアップ/復元は Makefile の `make db-backup`（`VACUUM INTO` → `docker compose cp`）/`make db-restore` で行う（[ADR-017](#adr-017-sqlite-を定期バックアップする) の dev 版）。
  - **「ホスト直 dev」フォールバック（`uv run uvicorn ...`）は compose とは別 DB（`./data/assetvane.db`）になる**ことを許容する（同じ DB を共有しない・dev での確認は原則 compose 側に寄せる）。
- **理由**:
  - bind mount は便利（ホストから素見えで開発しやすい）だが、macOS の gRPC-FUSE/virtiofs は SQLite の WAL/mmap セマンティクスを完全には満たさず、重い書き込みで**実際にページ破損が起きた**。dev の利便性より DB 健全性を優先する。
  - prod は環境が違う（ネイティブ Linux・FUSE なし）ので同じ対処は不要で、むしろ [ADR-017](#adr-017-sqlite-を定期バックアップする) のバックアップ可視性のため bind mount が望ましい。**環境差に応じて dev/prod で割る**のが妥当。
  - named volume の不可視性は Makefile のバックアップ/復元で吸収でき、運用上の不便は限定的。
- **代替案**:
  - **bind mount のまま WAL を切って `journal_mode=DELETE` にする** → 書き込み性能が落ち、[ADR-002](#adr-002-データベースは-sqlitewal-モード) の WAL 前提（読み書き同時・夜間バッチの連続書き込み）を崩す。却下。
  - **dev も prod も named volume に統一する** → prod はネイティブ Linux で FUSE 問題が無く、named volume にするとホストからバックアップが見えにくくなり [ADR-017](#adr-017-sqlite-を定期バックアップする) の運用が複雑化する。dev/prod で割れるのを許容して却下。
- **段階**: **実装済み（2026-06-22）**。`compose.yaml`（dev を named volume `assetvane-db` 化＋破損経緯コメント）・`compose.prod.yaml`（bind mount 維持）・`Makefile`（`db-backup`/`db-restore`）。破損 DB は host の `sqlite3 .recover` で復旧し `integrity_check=ok`（`lost_and_found` 無し＝孤児行ゼロ）・ユーザーデータと JP `daily_quotes` 193 万行は完全温存・失ったのは再取得可能な US 市場データのみ（`alembic_version=0021` 維持）。
- **関連**: [ADR-002](#adr-002-データベースは-sqlitewal-モード)（SQLite WAL・破損を起こした前提）・[ADR-021](#adr-021-開発本番ともコンテナdocker-composeで動かす)（Compose 運用・本 ADR が dev の DB 置き場の曖昧記述を named volume に確定）・[ADR-017](#adr-017-sqlite-を定期バックアップする)（バックアップ・prod が bind mount を保つ理由＝可視性／dev は `make db-backup`）・[ADR-005](#adr-005-db-に触れるのは-fastapi-のみnext-は-rest-経由)（DB に触れるのは FastAPI だけ）・[ADR-006](#adr-006-機械学習の学習は別-pcラズパイは推論のみ)（環境差で割る思想）。

## ADR-061: J-Quants の API キーとプランを env から DB＋WebUI（/settings）へ移管する

- **状況/問題**: [ADR-058](#adr-058-llm-プロバイダ面別-providermodel-設定を-env-から-dbwebuisettings-へ移管する)/[ADR-059](#adr-059-面別-reasoning_effort--codex-状態確認--embedding-接続の-dbwebui-化adr-058-拡張) で LLM・embedding の接続を env→DB+WebUI に移した。同じ思想で **J-Quants の `JQUANTS_API_KEY` と契約プラン `JQUANTS_PLAN`** も `/settings` から編集したい（ラズパイ初回デプロイ前に画面で鍵を入れ疎通確認まで完結したい・プラン移行も画面でやりたい）。当時は env 直読みで、`JQuantsAdapter.__init__` が `settings.jquants_api_key`/`settings.jquants_plan` を読み、6 箇所すべて引数なし `JQuantsAdapter()` で生成していた。
- **決定**:
  - **`jquants_config`（単一行・id=1・`api_key`/`plan`/`updated_at`・0024）に移管する**（`embedding_config` 同型）。api_key は平文（[ADR-001](#adr-001-単一ユーザー認証なし)・GET でマスク・write-only＝空送信は据え置き）。`GET`/`PUT /jquants/config` を新設し `/settings` の「J-Quants 設定」カードで編集。
  - **env を完全撤去**（`config.py` から `jquants_api_key`/`jquants_plan` を削除・env シードもしない）。初回は DB 未登録（鍵空）で、設定するまでバッチは `JQuantsError` で落ちる（runner が握って Discord 通知＝LLM 面未設定と同じ割り切り・[ADR-018](#adr-018-llm-障害時はフォールバックで縮退し夜間は通知して当日をスキップする)）。
  - **アダプタは settings 非依存にする**＝`JQuantsAdapter(api_key, plan="free")` を渡される純粋クライアントにし、DB 解決は `services/jquants_config`（`resolve_jquants_config`/`build_jquants_adapter` ファクトリ）に集約。全構成点（夜間バッチ 3 ジョブ・backfill・diagnostics・index の TOPIX フォールバック）はファクトリ経由。
  - **プランは 4 つ**（free/light/standard/premium）をドロップダウンに並べ、スロットル間隔は `adapters/jquants.py` の `_PLAN_INTERVALS` がプラン名から決める（[ADR-008](#adr-008-j-quants-は-v2x-api-key-ヘッダーを使うv1-は終了)・秒数を DB に持たない・standard≈0.5s/premium≈0.12s は概算）。`lead_lag` の遅延判定（`is_delayed`）と `meta.plan` も DB（`current_plan`）から読む。
  - **疎通テストは既存 `POST /diagnostics/jquants-test` を温存し DB 対応化**（`check_jquants(conn)`）。J-Quants 設定カードに畳んで再利用し、独立カードは削除。
- **理由**:
  - 鍵もプランも画面で管理できると、env を触らずデプロイ前に疎通確認・プラン移行ができる。LLM・embedding と作法を揃えれば設定の真実が DB に集まる。J-Quants は単一源なので `embedding_config` と同じ単一行が素直。
  - アダプタを settings 非依存にすると「鍵＋プランを渡されるクライアント」に純化でき、テストも fake キーを直接渡せる（[testing-strategy](testing-strategy)）。DB 解決を services のファクトリ 1 点に集約すると構成点が `build_jquants_adapter()` で揃う。
- **代替案**:
  - **env をフォールバックに残す（DB 優先・env 予備）** → LLM/embedding の全面移管と非対称になり、env と DB の二重管理が残る。env 完全撤去で揃えて却下（初回未設定はバッチ失敗で気づける）。
  - **移行で env 値を DB へ自動シードする** → Alembic が os.environ を読む変則運用になり、[ADR-058](#adr-058-llm-プロバイダ面別-providermodel-設定を-env-から-dbwebuisettings-へ移管する)/[ADR-059](#adr-059-面別-reasoning_effort--codex-状態確認--embedding-接続の-dbwebui-化adr-058-拡張) の「シードしない」と非対称。手動再設定に倒した。
- **段階**: **実装済み（2026-06-24・backend＋frontend）**。`0024_jquants_config`・`db/repo/jquants_config`・`services/jquants_config`（resolver＋ファクトリ）・`adapters/jquants`（settings 撤去・plan 引数・4 プラン）・`routers/jquants_config`（GET/PUT）・`routers/lead_lag`/`services/diagnostics`/`scripts/jquants_test`（DB 解決）・frontend（`lib/api/jquants-config`・`JquantsSettings` カード・独立疎通カード削除）。env から `JQUANTS_API_KEY`/`JQUANTS_PLAN` を撤去。pytest green。
- **補足（右上バッジの動的化・2026-07-01）**: ダッシュボード右上のトップバー（`Topbar.tsx`）が `Free・株価12週遅延（〜日付）` を**ハードコード**しており、`/settings` でプランを変えても実プランと乖離していた（プラン名・遅延幅とも静的で日付だけ client 側の擬似動的）。これを DB プラン由来に是正＝`/health` に `jquants:{plan, delay_days, configured}` を追加し（`services/jquants_config.plan_status`・既存の llm_cost と同一 conn・best-effort で安全既定に倒す）、Topbar がそれで描画する。**プラン別の遅延日数は `_PLAN_DELAY_DAYS` 定数**（free=84＝12週・light/standard/premium=0＝遅延なし・[jquants.md](jquants.md)／[ADR-008](#adr-008-j-quants-は-v2x-api-key-ヘッダーを使うv1-は終了)。フロントにハードコードせず backend が事実として配る＝[ADR-014](#adr-014-ai-に数値を計算させないpython-が事実を計算する)）。遅延の有無で配色（あり=warning・なし=muted）、`configured=false` は「J-Quants 未設定」表示（/settings 誘導）。**`fetch_quotes` の取得速度は無改修**（既に `build_jquants_adapter()`＋`_PLAN_INTERVALS` でプラン別に動的＝light 切替で次バッチから自動高速化するため、表示のみの是正）。
- **関連**: [ADR-058](#adr-058-llm-プロバイダ面別-providermodel-設定を-env-から-dbwebuisettings-へ移管する)/[ADR-059](#adr-059-面別-reasoning_effort--codex-状態確認--embedding-接続の-dbwebui-化adr-058-拡張)（同じ env→DB+WebUI 作法＝本 ADR の手本）・[ADR-008](#adr-008-j-quants-は-v2x-api-key-ヘッダーを使うv1-は終了)（V2・プラン）・[ADR-010](#adr-010-外部データはアダプタ越しに取るハードコードしない)（アダプタ越し）・[ADR-001](#adr-001-単一ユーザー認証なし)（平文・将来暗号化）・[ADR-018](#adr-018-llm-障害時はフォールバックで縮退し夜間は通知して当日をスキップする)（未設定はバッチ失敗を通知）・[ADR-005](#adr-005-db-に触れるのは-fastapi-のみnext-は-rest-経由)（DB に触れるのは FastAPI）・[data-model.md](data-model.md)・[api.md](api.md)・[jquants.md](jquants.md)。

## ADR-062: 知識カードを CORE/POLICY に続く第 3 の知識源として DB 化する（手法カードの再設計）

- **状況/問題**: AI アドバイザーに「投資判断の材料」をどう渡すかの概念が混線していた。設計対話（2026-06-26）で次が判明した。(1)「signal / tool / 手法カード」は別の軸（**データ / アクセス / 知識**）なのに同じ箱で語られていた。(2) `signals` テーブルの実態は **metric（連続スコア）**で、「PER<15 で割安」のような閾値判定は保存されておらず AI が `screen_*` Tool に閾値を渡す“行為”として生まれる（[ADR-026](#adr-026-シグナルは連続スコアの材料破壊的ゲートにしない)。閾値はユーザーでなく AI が決める前提に既になっていた）。(3)「method」が 3 つに分裂していた＝①手法（`quant/*.py` の計算コード）/ ②手法カード（`advisor/cards/*.md`・全カード常時注入）/ ③`method_cards` テーブル（data-model.md の将来予約・未実装）。(4) 手法カード運用が**危うい**＝①強制力ゼロ（プロンプト内の散文で LLM が無視/誤用しうる）②LLM の一般知識と重複（「PER 15 倍が目安」は既知）③全カード常時注入でスケールしない（[ADR-048](#adr-048-バリュエーション判断基準を参照知識カードで持つ) が progressive disclosure を planned と明記）④コードとの境界が曖昧（計算ルールを散文に書くと再現性が消える・[ADR-016](#adr-016-手法は必ずテスト済みコードで実装する-ta-lib-は使わない)）。利用者の要望は「知識を**増やしやすく**（YouTuber 動画要約等を登録→関連時に surface）・**UI で管理**・**AI が審査して振り分け**・必要なら**実装待ち**ステータス・**RAG は近スコープの次フェーズ**（件数ゲートではない）」。
- **決定**:
  - **3 軸を分けて命名する**。データ＝metric/signal、アクセス＝tool、知識＝カード。**signal は metric として残す**（情報として参照・ADR-026 のまま・破壊的ゲートにしない）。閾値は AI が定性判断する（落とすのは「閾値ゲートで機械的に絞る／ユーザーが閾値を決める」前提だけ）。**[ADR-014](#adr-014-ai-に数値を計算させないpython-が事実を計算する)（AI に数値を計算させない・計算はコード）は死守**する。
  - **カードを 1 概念に collapse する**。①**規律・ペルソナ**（「単一指標で決めるな」「捏造するな」「業種内で比較」「市場内ランクに閉じる」）は **CORE（`core_prompt.md`）へ吸収**（量が増えないので常時注入で困らない・もう「カード」と呼ばない）。②**知識**（市場文脈・外部メモ・手法の解釈）は **`knowledge_cards` テーブル（DB・UI 管理・RAG）**。これが唯一の「カード」。③**一般教科書知識**（PER 15 倍が目安 等）は**書かずに LLM に任せる**。
  - **テーブルは 1 つ（`knowledge_cards`）・手法カタログ表は作らない**。data-model.md の将来予約 `method_cards`（doc のみ・未実装＝消すデッドコードは無い）を新スキーマで実体化＋改名（"method" の 3 分裂を解消）。手法↔計算の索引役は `linked_signal_type` 列に畳む（別カタログ表は不要・計算は `quant/*.py` のまま）。列＝`title`/`body`/`when_to_apply`（適用条件＝retrieval キー）/`status`/構造タグ（`level`/`sector17_code`/`theme`）/`linked_signal_type`/`quant_note`/`always_inject`/`source`/`embedding` 3 列/`created_at`/`updated_at`（0025）。
  - **追加フロー＝AI 審査トリアージ**。UI でカード草案を入れると AI が分類して status を振り分ける（「コード/カード/CORE/LLM 一般知識」の振り分け規律＝弱点④の自動防波堤）: `rejected`（LLM 一般知識でカード不要）/ `to_core`（規律→CORE 誘導）/ `needs_quant`（未計算の指標値が要る＝quant 実装待ち・`quant_note` に必要計算）/ `active`（既存値で成立する具体知識）。**active 化（本番助言に効く）は AI ではなく人間が最終承認**（カードは以後の全助言を左右するため・[ADR-009](#adr-009-提案承認制発注はしない決めるのはユーザー)）。AI 審査はトリアージ＋下書きまでで、verdict が `active` でも status は draft 据え置き（`POST /cards/{id}/activate` が人間承認）。**AI 審査は独立 LLM 面 `triage` を使う**（`FACES` の 5 面目＝[ADR-058](#adr-058-llm-プロバイダ面別-providermodel-設定を-env-から-dbwebuisettings-へ移管する) 拡張）。tag 系（theme/polarity）と同じ「単発 JSON 分類・沈黙 skip」型だが、triage は**低頻度かつ結果が重い**（カードの行き先を誤ると全助言に波及）ので、tag 系と独立に強いモデルを割り当てられるよう面を分けた。未設定なら沈黙 skip でカードは draft のまま（`/settings` の「カード審査」面に provider/model を割り当てると有効化）。
  - **カードの 3 パターン**（quant 要否）＝ `linked_signal_type`＋`status` でモデル化: ①単独（解釈だけ・既存値で成立・null/active）②既存 quant に紐づく（既実装 signal の読み方・active）③新 quant が要る（needs_quant＝実装待ち）。
  - **注入/RAG は 2 フェーズ・件数非依存**。フェーズ1（足場・本 ADR で実装）＝テーブル＋保存時 best-effort 埋め込み（`when_to_apply`・`adapters/embedding` 再利用＝embed_news 同型）＋夜間 `embed_cards` ジョブ＋AI 審査＋追加 UI。注入は `services/knowledge_cards.load_active_card_texts()` が status='active' を全注入し、旧 `method_cards.py` の常時注入を置換（`build_messages` の `method_cards` 引数→`knowledge_cards`）。フェーズ2（近スコープの次）＝意味検索 retrieval（構造タグ事前フィルタ→`when_to_apply` cosine top-K）＋`search_cards` Tool＋軸別トリガー（チャット=質問 / 夜AI=分析対象銘柄の sector/theme）＝[ADR-045](#adr-045-ニュースの意味検索を段階的に入れる)（ニュース意味検索 段階A）のクローン。`embedding` 列はフェーズ1 から焼く（null 放置にしない）。
- **理由**:
  - 3 軸の混同が利用者の混乱の正体だった。signal は元から「破壊的ゲートにしない連続材料」（ADR-026）で、閾値を AI が screen 時に渡す設計なので、利用者の「閾値は AI に判断してほしい」は**今の設計のまま叶う**（ADR-009 の「決めるのはユーザー」は最終的な売買のことで screening 閾値ではない）。
  - カードを CORE/知識/LLM の 3 行き先に振り分ける規律は、危うさの弱点①②④の裏返し。規律は有限なので CORE に常時注入で困らず、知識だけが無限に増えるので RAG が要る＝「カード」を知識 1 種に固定すると弱点③（スケール）も知識側だけ on-demand 化すれば済む。
  - `method_cards`（予約）は元から「索引＋カタログ＋参照知識」を 1 表に詰め `embedding`/`when_to_apply`/`linked_signal_type` まで設計済みで、利用者が欲しい「DB 管理・RAG・コード連携の知識カード」そのもの。実体化＋改名で "method" の 3 分裂も解消する。
  - AI 審査をトリアージ係にすると弱点④（コードとの境界の曖昧さ）を毎回 AI が自動で判定でき、人間は active 化だけ承認すれば in the loop でいられる（暴走しない・silent self-edit はしない）。
- **代替案**:
  - **カード 2 層化（規律カード＋知識カード）** → 利用者が「規律はカードの外＝CORE に出せば 1 概念で済む」と指摘し却下。collapse の方がスッキリし弱点③も知識側だけで閉じる。
  - **RAG を件数で gate（〜50 枚まで全注入）** → 利用者が「件数の問題でなく、登録知識が関連時に surface するのがカードの価値」と指摘。近スコープの次フェーズに前倒し（足場→次の 2 フェーズ）。
  - **別途「手法カタログ表」を持つ** → デッドコード/二重概念になる。`linked_signal_type` を knowledge_cards に畳んで 1 表に統合。
  - **signal を生の指標＋カードに寄せて AI に全判断委譲（事前計算 signal を廃止）** → ADR-016（手法は再現性のためコード）と非対称になり backtest が壊れる。signal は metric として維持。
- **段階**: **フェーズ1 実装済み（2026-06-26・backend＋frontend）**。`0025_knowledge_cards`・`db/repo/knowledge_cards`・`advisor/card_triage`・`batch/jobs/embed_cards`（NIGHTLY 追加）・`routers/cards`（GET/POST/PUT/DELETE/triage/activate）・`services/knowledge_cards`（注入）・`scripts/seed_knowledge_cards`（旧 jp-market-context を market カードへ移行）・`build_messages`（`method_cards`→`knowledge_cards`）・CORE に規律追記・`advisor/method_cards.py`＋`advisor/cards/` 削除・frontend `/cards`＋`lib/api/cards`。pytest（repo/triage/api/embed_cards で 25 件追加）・ruff/pyright green。**triage は独立 LLM 面に分離済み**（`FACES` 5 面目・[ADR-058](#adr-058-llm-プロバイダ面別-providermodel-設定を-env-から-dbwebuisettings-へ移管する) 拡張・tag 系と別モデル可）。**フェーズ2（RAG retrieval）も実装済み（2026-06-26）**＝`repo.search_knowledge_cards`（`vec_distance_cosine`・active のみ・距離昇順・level/sector17/theme 事前フィルタ）／`services.knowledge_cards.retrieve_cards`＋`load_card_texts_for_injection`（**ambient**＝always_inject/market/general/level なしは常時注入＋**chat は最新ユーザー発話で意味検索 top-K 追加**／夜AI は ambient のみ＋`search_cards` Tool で深掘り／機能オフは全 active fallback で graceful）／`search_cards` Tool〔min_phase=4・`SearchCardsArgs`・handler は service 橋渡し〕。pytest 6 件追加（実 `vec_distance_cosine`＋embedding mock）。**追補も実装済み（2026-06-26・retrieval 刷新＋AI 補助）**＝① 注入を**純 retrieval 化**（level ベースの ambient を廃し `always_inject` フラグのみ常時注入＝chat は最新発話で意味検索／夜AI は always_inject＋`search_cards`／機能オフは全 active fallback）。② 埋め込み元を when_to_apply から **title+when_to_apply+body の合成テキスト**へ（when_to_apply 任意化＝本文だけでも検索に乗る・埋め込み元が変われば再埋め込み）。③ **AI ドラフト補助**＝本文だけ入力→`assist_card` が title/when_to_apply/level を生成＋審査（`POST /cards/assist`・title 任意化・空なら本文先頭で代替）。④ **weight 列**〔0026・既定 1.0〕で retrieval ランク/注入順を `distance/weight` 重み付け（古い/信頼度低を下げて削除せず生かす・手動編集＋チャット AI が承認制で変更〔別段〕）。⑤ created_at（不変・追加日）を注入テキストに添えて AI が鮮度を解釈。⑥ **チャットからカード整備 tool（承認制）**＝`propose_card`〔会話から知識カードを **draft 起票**→人間が /cards で active 化〕・`adjust_card_weight`〔weight 変更を `proposals(kind='card_weight')` で起票→/proposals で承認すると `resolve_proposal` が反映〕。handler は read-only 検証 only・実書き込みは `persist_card_ops_from_tool_runs`（W2＝chat router/nightly が journal/proposal と同一トランザクションで束ねる・propose_trade と同型）。**承認制で実装（後で weight 直接化＝ゲートを外すだけで容易）**。pytest 追加（persister/resolve/handler）・ruff/pyright/tsc/Biome green。**追補（UI「雑追加」リデザイン・決定 2026-06-30・実装は後続 PR）**＝知識カードの追加を「本文を貼るだけ」へ簡素化する（利用者要望「フィールドが多い・適用条件を考えたくない・良さげな記事を雑に放り込みたい・使いどころは AI に判断させたい」）。① **追加フォームは本文＋source〔任意・URL〕＋「追加」だけ**（title/when_to_apply/level の入力欄と「AI に整えてもらう」ボタンを撤去＝1 アクション化）。② 追加時に **同期**で `assist_card`（`triage` 面・1 回の LLM で title/when_to_apply/level 生成＋verdict 審査を兼ねる）を走らせ、verdict を status へ反映（rejected/to_core/needs_quant は自動・**active 候補は draft 留置＝人間ワンクリック承認**で本番助言に効く＝[ADR-009](#adr-009-提案承認制発注はしない決めるのはユーザー) 維持）。③ **title は AI 生成**（本文先頭の切り出しはやめる＝create 経路の `_fallback_title` を外す）。④ **`triage` 面未設定/AI 失敗は graceful**＝本文は必ず draft 保存・title 空で「AI未整形」表示・行に「AI で整える」再試行＋`/settings` 誘導。**chat 面へのフォールバックはしない**（triage の面分離＝[ADR-058](#adr-058-llm-プロバイダ面別-providermodel-設定を-env-から-dbwebuisettings-へ移管する) を保つ＝利用者判断 2026-06-30）。⑤ **when_to_apply は内部に残すが UI から消す**（AI が埋める・埋め込みキーの足しにのみ使用・注入テキストには元から出していない・migration 不要）。⑥ **triage の reason を保存**（`0028_knowledge_cards_triage_reason`＝`triage_reason` 列追加）して再読込後も判定理由が残る。一覧は**承認待ち(draft) を上に・verdict＋reason を常表示**（rejected/to_core/needs_quant はタブの奥）。影響境界: チャットの `propose_card` は従来どおり（会話から AI が draft 起票・auto-triage なし＝既に AI 由来）。`CardCreateIn` の narrowing は UI フォーム専用で、`insert_knowledge_card_tx` を直叩きする `persist_card_ops_from_tool_runs` には無影響。コストは「1 回貼るたびに `triage` 面の LLM 1 回（同期）」＝利用者の意図どおり許容。関連: [ADR-018](#adr-018-無人運用の障害時方針失敗を黙って放置しない)（graceful skip）・[ADR-014](#adr-014-ai-に数値を計算させないpython-が事実を計算する)（AI は解釈のみ）。
- **修正（2026-07-01・全体レビュー）**: ① **即時埋め込みが async 経路で 100% 死んでいた（#9）**＝`POST /cards`・`POST /cards/{id}/assist`（async）が `embed_card_best_effort`（内部 `asyncio.run`）を実行中イベントループ内で呼び RuntimeError→握り潰しで、この ADR が謳う「保存時 best-effort 即時埋め込み」が機能せず追加カードは夜間まで意味検索に載らなかった。→ `embed_card_best_effort_async`（`await embed_texts`・services/news.py 同型）を新設し async 経路は await、sync 経路（`PUT /cards`）は薄いラッパで維持。② **active カードの無言降格を防止（#3）**＝`reassist` が現在 status を見ず AI verdict='active'（=status draft 解決）で active を上書きしていた。「active 化は人間承認」（[ADR-009](#adr-009-提案承認制発注はしない決めるのはユーザー)）は保つが、一度承認した active を AI 整形で無言降格させ注入から落とすのは意図に反するため、現在 active かつ verdict=='active' なら active を温存。③ **デッドコード整理（#15）**＝`assist_card` に統合済みの `triage_card`/`TriageResult`/`_parse_triage_response` を撤去（テストは `_parse_assist_response` の直接検証へ転換）。詳細は [review-2026-07-01.md](../tasks/review-2026-07-01.md)。
- **追補（2026-07-02・銘柄スコープ＝銘柄粒度の知識軸）**: 個別銘柄特有の知見（アノマリー等）を `level='stock'` として厳密に紐づけるため `knowledge_cards` に `market`＋`code`（nullable・`0033_knowledge_cards_stock_scope`＋`ix(market,code)`・backfill なし）を足す。ドシエ（`stock_dossiers`・毎晩上書きの揮発的な事実要約＝ADR-020）とは別で、**蓄積する解釈的知見**はこちらに置く（上書きで消えない・active 化は人間承認）。`code` の同一性は**常に決定論**で握る＝フォームは明示欄＋実在検証（未知 400）・`propose_card` は会話の tool 文脈由来（未知 drop＝ADR-052 同型）で、いずれも名前推測をしない（ADR-050/056 の grounding 規律）。注入は **exact-match 最優先**＝chat は `focus.code`、夜 AI は注目候補の code ぶんを**意味距離を問わず**注入し、銘柄ノート（code 付き）は**汎用の意味検索プール／ambient から除外**して他銘柄会話への漏れを防ぐ（`code` あり ⟺ `level='stock'`・`code` 付きは `always_inject` 禁止）。多銘柄共通は既存の `sector17_code`/`theme`、任意バスケットは複製 or 将来 `card_stocks` へロスレス昇格（スカラーは join の部分集合）。`search_cards`/`propose_card` に `code`/`market`、`/cards`（`CardCreateIn.code`）に銘柄欄、銘柄詳細に「この銘柄のノート」導線（`focus.code` プリフィル）を追加。ADR-062「夜=ambient のみ」を銘柄ノートに限り決定論注入する形で小幅拡張（ADR-067 の直接注入精神と整合）。`FocusRef` に market を通すのは将来（当面 code 一致で JP 数字系/US 英字系は衝突しない）。設計正本＝[stock-notes-design.md](../tasks/stock-notes-design.md)。ATDD＝`tests/test_stock_scoped_cards.py`（13 件）・pytest/ruff/pyright/tsc/Biome green。
- **関連**: [ADR-015](#adr-015-システムプロンプトは不変-core可変-policy-に分離する)（CORE/POLICY＝本 ADR は第 3 の知識源 knowledge_cards を足す）・[ADR-016](#adr-016-手法は必ずテスト済みコードで実装する-ta-lib-は使わない)/[ADR-020](#adr-020-個別銘柄ドシエ定性ファンダ調査-1銘柄1レポートを更新し続ける)/[ADR-048](#adr-048-バリュエーション判断基準を参照知識カードで持つ)（旧・手法カード＝規律は CORE・知識は DB へ移行で改訂）・[ADR-026](#adr-026-シグナルは連続スコアの材料破壊的ゲートにしない)（signal は metric・閾値は AI）・[ADR-014](#adr-014-ai-に数値を計算させないpython-が事実を計算する)（計算はコード・死守）・[ADR-009](#adr-009-提案承認制発注はしない決めるのはユーザー)（active 化は人間承認）・[ADR-045](#adr-045-ニュースの意味検索を段階的に入れる)（RAG のクローン元）・[ADR-005](#adr-005-db-に触れるのは-fastapi-のみnext-は-rest-経由)（DB に触れるのは FastAPI）・[data-model.md](data-model.md)・[api.md](api.md)・[advisor.md](advisor.md)。

## ADR-063: CORE 論証規律の強化＋業績の質シグナル family（外部スキル集 financial-services の蒸留）

- **状況**: Anthropic 公式の金融サービス向けスキル/エージェント集（`financial-services`・md/yaml/json のみ）を AssetVane に転用できるか調査した。大半は機関業務（IB/PE/監査）・Excel/PPT 出力・教科書知識で**個人投資家ダッシュボードには不適**。だが ①equity-research 由来の**論証規律**（Bull/Base/Bear・catalyst・前提崩れ条件・確信度）と、②earnings-analysis 由来の**業績の「質」を見る分析ノウハウ**の 2 系統だけは価値があると裏取りできた。②は機関ノウハウの多くが AssetVane に該当データが無く [ADR-014](#adr-014-ai-に数値を計算させないpython-が事実を計算する)（Tool の無い数値は述べない）で使えないため、**源が実在し入手可能なものだけ**に絞った。
- **決定**:
  - **論証規律は CORE 要素⑤に散文追記**（[ADR-062](#adr-062-知識カードを-corepolicy-に続く第-3-の知識源として-db-化する手法カードの再設計) の「規律は CORE へ吸収」＝triage の `to_core` 相当）。**知識カードにはしない**（毎回の提案で発火すべき普遍規律で、検索ヒット時のみ注入のカードでは大半の提案で抜ける）。追記＝買い/売り提案は **Bull/Base/Bear を定性的に併記**（確率の数値は作らない＝要素③）・**catalyst を名指し**・**前提崩れ条件（invalidation）を最低 1 つ明記**・**確信度を高/中/低で明示**（パーセント不可）。
  - **業績の質ノウハウは「データ源→quant→Tool」のコードで実装し、解釈レイヤだけ CORE 要素②に 1〜2 行で集約**（カードは将来の業種依存ニュアンス用に温存）。**各 Tool が landing してから CORE② に確認指示を書く**（Tool が無いうちに書くと ADR-014 違反）。採用 4 つ＝**#8 利益質の分解**（追加データ不要・既存 `*_growth_yoy`＋利益率で売上/利益率/株数・一時要因の由来を切り分け）／**#4 会社ガイダンス**（J-Quants `fins/summary` の予想カラム抽出＝予想 vs 実績の beat/miss・上方/下方修正）／**#2 売掛・在庫の質**（JP=EDINET CSV の BS 抽出／US=yfinance balance_sheet）／**#7 訂正報告フラグ**（EDINET の docTypeCode=130 を拾う）。
  - **見送り**: insider 売買シグナル（源なし）・DCF/Terminal Value/OpEx モデル検証（モデル実装＋前提入力で重く個人ダッシュボードの philosophy と不一致）・アナリストコンセンサス beat/miss（JP 源なし・US yfinance 不安定）。
- **理由**: 規律（出力の作法）と知識（解釈）と計算（事実）は置き場所が違う（[ADR-014](#adr-014-ai-に数値を計算させないpython-が事実を計算する)/[ADR-016](#adr-016-手法は必ずテスト済みコードで実装する-ta-lib-は使わない)/[ADR-062](#adr-062-知識カードを-corepolicy-に続く第-3-の知識源として-db-化する手法カードの再設計)）。論証規律は毎回効かせたい普遍規律ゆえ CORE（常時注入・版管理で drift 防止＝[ADR-015](#adr-015-システムプロンプトは不変-core可変-policy-に分離する)）。業績の質は「Tool が事実（数字）を計算し、CORE が懐疑的クロスチェックの作法を強制し、recency 等の解釈は LLM」に三分割すると ADR-014 を割らない。データ可用性ゲートを置くことで「使えそうだが源が無い」ルールを CORE に書いて捏造を誘発する罠を避けた。
- **代替案**: **論証規律も knowledge_cards に**（`when_to_apply` で UI 編集可）→ 検索ヒット時しか出ず毎回の提案で抜ける・[ADR-062](#adr-062-知識カードを-corepolicy-に続く第-3-の知識源として-db-化する手法カードの再設計) の「規律は CORE へ」を逆行＝却下。**proposals.body に scenarios/catalysts JSON を構造化**→ migration＋frontend 波及で蒸留の範囲超え＝今回は見送り（将来機能）。**機関ノウハウを全部カード化**→ 教科書知識（triage で rejected 相当）と AssetVane が源を持たない数値が大半＝却下。
- **段階**: **Layer A（プロンプトのみ・データ依存なし）実装済み（2026-06-30）**＝`core_prompt.md` 要素⑤に論証規律・要素②に #8 利益質の分解。**#7 訂正報告フラグ実装済み（2026-06-30）**＝`0027_edinet_restatements`（doc_id 冪等 append-only 台帳）／`adapters/edinet` に docType=130 定数／`fetch_edinet_descriptions` クロールが 130 を本文取得せず記録（要約 cap と独立）／`repo.record_edinet_restatement`＋`get_latest_restatement_date`／`get_valuation` が `last_restatement_at`（事実=最新提出日・recency 解釈は LLM）を中継／CORE② に訂正の節を追記。pytest 4 件追加・ruff green・migration を実 DB で適用確認。**#4 会社ガイダンス実装済み（2026-06-30）**＝実機プローブ（7203/6758/9984 の `/v2/fins/summary` 生レスポンス）で予想カラムを確定（当期FY予想 `FSales`/`FOP`/`FNP`/`FEPS` は各四半期開示に standing で載り、**FY実績行では空**＝実績は別列・予想を出さない会社〔例 9984〕は全空）／`0029_financials_guidance`（`financials` に `forecast_net_sales`/`forecast_operating_profit`/`forecast_profit`/`forecast_eps`・`valuation_snapshots` に `op/profit_forecast_achievement`〔beat/miss〕・`op/profit_forecast_revision`〔上方/下方修正〕）／`adapters/jquants._normalize_financial` が予想 4 列を `_first` で抽出（空文字→None）／`quant.valuation` の純関数 `forecast_achievement`（実績÷予想・予想≤0 は None）・`forecast_revision`（新÷旧−1）・`forecast_guidance(rows)`（最新完了FY実績÷その期最終 standing 予想で beat/miss、進行中FY予想の直近 2 開示で上方/下方修正・DB非依存）／`repo.get_recent_financials_by_code`＋`services.valuation` が夜間 calc_valuation で焼く／`get_valuation` が `op/profit_forecast_achievement`・`op/profit_forecast_revision` を中継／CORE② に「会社予想との比較」節を追記。pytest 約 12 件追加・ruff/pyright green・migration を実 DB で適用確認。**#2 売掛/在庫は [ADR-064](#adr-064-売掛在庫の質業績の質-2は-edinetdbjp-の構造化財務で実装し接続設定を-dbwebui-化する) で着工**（着工調査で手元キーが公式 EDINET でなく第三者 `edinetdb.jp` のものと判明＝JP は生 XBRL でなく edinetdb.jp の構造化財務 `trade_receivables`/`inventories`/`revenue`/`gross_profit`・US は yfinance `balance_sheet` を使う方針へ更新）。残る B（#2）の CORE② 追記は対応 Tool の landing と同 PR で行う。
- **関連**: [ADR-014](#adr-014-ai-に数値を計算させないpython-が事実を計算する)（事実はコード・解釈は LLM）・[ADR-015](#adr-015-システムプロンプトは不変-core可変-policy-に分離する)（CORE は規律の置き場）・[ADR-016](#adr-016-手法は必ずテスト済みコードで実装する-ta-lib-は使わない)（手法はテスト済みコード）・[ADR-062](#adr-062-知識カードを-corepolicy-に続く第-3-の知識源として-db-化する手法カードの再設計)（規律/知識/計算の三分割）・[ADR-056](#adr-056-edinet-を-jp-の事業説明テキスト源にする)（EDINET 提出日クロール＝#7 の相乗り先）・[ADR-048](#adr-048-バリュエーション判断基準を参照知識カードで持つ)（valuation 事実）・[data-model.md](data-model.md)・[api.md](api.md)・[advisor.md](advisor.md)。

---

## ADR-064: 売掛/在庫の質（業績の質 #2）は edinetdb.jp の構造化財務で実装し接続設定を DB+WebUI 化する

（2026-06-30）

- **状況**: [ADR-063](#adr-063-core-論証規律の強化業績の質シグナル-family外部スキル集-financial-services-の蒸留) #2（売掛/在庫の質）の着工条件「JP は EDINET 実 CSV の BS 要素 ID を 1 件確認」を満たすため `.env` の `EDINET_API_KEY` で実 API を叩いたところ、公式 EDINET API（`api.edinet-fsa.go.jp/api/v2`）が一貫して 401（invalid subscription key）を返した。切り分けの結果、**手元のキーは公式 EDINET のものではなく第三者の付加価値サービス `edinetdb.jp`（EDINET DataBase・キー接頭辞 `edb_`・認証は `X-API-Key` ヘッダ・base `https://edinetdb.jp/v1`）のものだった**。旧キーの時点から本環境に公式キーは無く、**[ADR-056](#adr-056-edinet-を-jp-の事業説明テキスト源にする有報事業の内容-を要約して-company_descriptions-に持つ) の公式 EDINET 連携（テーマタグ段階C・#7 訂正フラグのクロール）はこの環境で一度も認証が通っていない**疑いが濃い（ユニットテストはネットに出ないため緑のまま）。一方 `edinetdb.jp` は実測で、`edinet_code`（例 `E03006`）直引きの `GET /companies/{edinet_code}/financials` が `trade_receivables`（受取債権）・`inventories`（棚卸資産）・`revenue`・`gross_profit`（COGS=revenue−gross_profit）等を**正規化済み（JP GAAP/IFRS 吸収）**で返すと確認した（近年の行に詳細 BS・古い年はサマリのみ＝年次で可変）。レート制限は**日 100・月 600 リクエスト**（無料枠・`x-ratelimit-*` ヘッダで残量が分かる）。
- **決定**:
  - **#2 の JP 側は公式 EDINET の生 XBRL/CSV パースを採らず、`edinetdb.jp` の構造化財務を使う**。`trade_receivables`/`inventories`/`revenue`/`gross_profit` を取得し、quant 純関数で DSO（売掛金回転日数）・DIO（在庫回転日数）・受取債権 YoY と売上 YoY の乖離・在庫 YoY と売上 YoY の乖離（事実）を出す。押し込み・滞留の疑いの解釈は LLM（[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け)/[ADR-016](#adr-016-手法は必ずテスト済みコードで実装する-ta-lib-は使わない)）。**US 側は yfinance `balance_sheet`（`Receivables`/`Inventory`）＋`income_stmt`（`Total Revenue`/`Cost Of Revenue`）**で同型。
  - **`edinetdb.jp` の接続設定（api_key・plan）を env でなく DB（`edinetdb_config` 単一行）＋ `/settings` の WebUI で管理する**（[ADR-061](#adr-061-j-quants-の-api-キーとプランを-env-から-dbwebuisettings-へ移管する) の J-Quants 設定と同型・GET はマスク・write-only・疎通テスト同居）。プランは当面 **free**、将来 **pro** を検討。plan 別にレート制限の挙動を変える（`_PLAN_LIMITS`＝free:{日100,月600}・pro:{将来値}）が、**実予算の enforce はレスポンスの `x-ratelimit-*-remaining` ヘッダで行う**（plan 定数は throttle 間隔と夜間ソフト上限の目安）。
  - **対象は watchlist＋holdings に限定**（月 600 を尊重・低頻度・銘柄単位）。全ユニバース巡回には載せない。`stocks.edinet_code` を追加し `edinetdb.jp /companies` 一覧から sec_code↔edinet_code を解決・キャッシュ。
  - **公式 EDINET（[ADR-056](#adr-056-edinet-を-jp-の事業説明テキスト源にする有報事業の内容-を要約して-company_descriptions-に持つ) のテーマタグ段階C・#7）は本 ADR の対象外で据え置き**。実データで必要になったら「公式 EDINET キーを別途発行」か「edinetdb.jp 有料枠＋`/text-blocks` 等への移行」を別 ADR で判断する。**命名は公式=`edinet`／第三者=`edinetdb` で分離**して混同を防ぐ。
- **理由**: #2 の最大の脆さは生 XBRL の BS 要素 ID 特定（JP GAAP/IFRS/会社独自タクソノミ差）で、[ADR-063](#adr-063-core-論証規律の強化業績の質シグナル-family外部スキル集-financial-services-の蒸留) が着工ゲートを置いたのもそこ。`edinetdb.jp` は正規化済みデータを銘柄コード直引きで返すため、この脆さとゲートが消え、AssetVane の銘柄単位バリュエーションと素直に噛み合う。第三者依存リスクはアダプタ越し（[ADR-010](#adr-010-データソースはアダプタ越しにする)）で封じ込め、単一ユーザーの個人ツール（[ADR-001](#adr-001-単一ユーザー認証なし)）では許容範囲。月 600 制約は watchlist/holdings 限定＋キャッシュ＋ヘッダ実予算 enforce で吸収できる。接続設定の DB+WebUI 化は [ADR-061](#adr-061-j-quants-の-api-キーとプランを-env-から-dbwebuisettings-へ移管する) で確立した「秘密と切替は env でなく DB」の踏襲。
- **代替案**: **公式 EDINET キーを取り直して生 XBRL をパース**→ #2 の脆さを丸抱え・着工が遅い・個人ツールに過剰＝今回は採らない（将来テキスト系で公式が要るなら別途）。**edinetdb.jp 全面移行（J-Quants 財務も置換）**→ J-Quants は [ADR-008](#adr-008-j-quants-は-v2x-api-key-を使うv1-は使わない) で確立・稼働中で、月 600 では全ユニバース夜間取得が破綻＝範囲超え。**接続設定を env のまま**→ [ADR-061](#adr-061-j-quants-の-api-キーとプランを-env-から-dbwebuisettings-へ移管する) と非対称・WebUI で plan/key を切れない＝却下。
- **段階**: 実装着手（2026-06-30）。Migration `0030_edinetdb_config`（`edinetdb_config`＋`stocks.edinet_code`）・`0031`（`valuation_snapshots`/`us_valuation_snapshots` に DSO/DIO・受取債権/在庫 YoY 乖離列）／`adapters/edinetdb.py`（`EdinetDbAdapter`）／`services/edinetdb_config.py`（resolve＋`build_edinetdb_adapter`＋`_PLAN_LIMITS`）／`routers/edinetdb_config.py`＋`/diagnostics/edinetdb-test`／`/settings` カード／`quant/valuation.py` 純関数／夜間ジョブ（watchlist/holdings 限定）／`get_valuation`・`get_us_valuation` 中継／CORE② に「売掛金・在庫の質」節。ATDD で進める。
- **修正（2026-07-01・全体レビュー）**: #2 列（DSO/DIO・受取債権/在庫 YoY）が**毎晩 NULL に潰れて実質機能していなかった**のを直した。真因は共通ヘルパ `db/repo/_common._upsert` が「衝突キー以外の**全列**を EXCLUDED 更新」する仕様で、`calc_valuation`/`calc_us_valuation` の毎晩の全銘柄 UPSERT（行 dict に #2 列を含まない）が、後段 cadence ジョブ `calc_(us_)receivables_inventory` が UPDATE 充填した #2 列を潰していた（NIGHTLY 順は calc_valuation 先→ cadence ジョブ後・後者は `edinetdb_refresh_interval_days=7` で殆どの夜 skip ＝ 7 晩に 1 晩しか #2 が生きない）。`sync_master` の `upsert_stocks` も同根で `stocks.edinet_code` を毎晩潰し、edinetdb sweep の成果破棄＋API 無駄叩きになっていた。修正＝`_upsert` に `partial=True`（**rows に実在する列の和集合だけ**を EXCLUDED 更新し他列は温存）を追加し、`upsert_valuation_snapshots`/`upsert_us_valuation_snapshots`/`upsert_stocks` の 3 本に適用（既に同型を手書きしていた `upsert_us_stocks` も `_upsert(partial=True)` へ集約＝重複解消）。ATDD＝`tests/test_partial_upsert.py`（①主要UPSERT→②担当列UPDATE→③翌晩再UPSERTで②列が温存される、を JP/US/edinet_code の 3 本で固定）。
- **関連**: [ADR-063](#adr-063-core-論証規律の強化業績の質シグナル-family外部スキル集-financial-services-の蒸留)（#2 の親）・[ADR-061](#adr-061-j-quants-の-api-キーとプランを-env-から-dbwebuisettings-へ移管する)（接続設定の DB+WebUI 化の手本）・[ADR-056](#adr-056-edinet-を-jp-の事業説明テキスト源にする有報事業の内容-を要約して-company_descriptions-に持つ)（公式 EDINET＝据え置きの別系統）・[ADR-010](#adr-010-データソースはアダプタ越しにする)（アダプタ越し）・[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け)（事実はコード・解釈は LLM）・[ADR-005](#adr-005-db-に触れるのは-fastapi-のみnext-は-rest-経由)（DB に触るのは FastAPI）・[ADR-001](#adr-001-単一ユーザー認証なし)（単一ユーザー）・[data-model.md](data-model.md)・[api.md](api.md)・[advisor.md](advisor.md)。

---

## ADR-065: AI Advisor に専用大画面ページ /advisor を追加し会話状態を共有する（OPEN-I 撤回・ADR-024 補強）＋知識ノートの壁打ち導線

（2026-06-30）

- **状況**: AI Advisor で「文章を渡して知識ノート（知識カード）を作らせたい。ただし即起票でなく、ある程度壁打ちして『この内容で作る？』と確認してから作りたい」「メニューの Advisor を踏んだら大きいチャット画面で話したい」という要望が出た。調査の結果、**カード起票 Tool は既に存在する**＝`propose_card`（[ADR-062](#adr-062-知識カードを-corepolicy-に続く第-3-の知識源として-db-化する手法カードの再設計) 追補・min_phase=4）が draft 起票→`/cards` で人間が active 化（承認制・[ADR-009](#adr-009-提案承認制発注はしない決めるのはユーザー)）。仕組みは揃っており不足は ①壁打ちの作法（合意してから起票）が CORE に無い ②チャット内にカード起票のフィードバックが無い（`journal_id` はインライン表示されるがカードは未可視化）③大画面の専用チャットページが無い（[OPEN-I 確定](phase-specs/phase3-spec.md)＝「Advisor は専用ページを作らずチャット起動トリガ」で、`nav.ts` は常駐フローティングを開くだけ）の 3 点だった。
- **決定**:
  - **専用大画面ページ `/advisor` を新設し、メニュー「Advisor」の遷移先にする**（**OPEN-I の「専用ページ無し」を撤回**）。会話状態は新設 Context `AdvisorChatProvider`（root layout 直下）に持ち上げ、**常駐フローティングと `/advisor` が同一の会話を見る**（[ADR-024](#adr-024-ai-advisor-チャットを全ページ常駐にするフローティング) のページ遷移会話保持を Context の生存で担保）。`/advisor` 上ではフローティング窓を非表示にして二重表示を避ける。会話本体は `ChatConversation` に抽出してフローティング/ページで共用。フローティングは Policy 等の「チャットで調整」導線から従来どおり開ける（`advisor-bus` 据え置き）。
  - **知識ノート作成は承認制（draft→activate）を維持**し、入口は直接フォーム（`POST /cards`）と `propose_card` の**両方を残す**（役割分担＝貼って即ファイル／壁打ちして練る・整形の統一はしない）。
  - **壁打ちの作法は CORE プロンプト（要素④）に散文 1 節で追加**（[ADR-062](#adr-062-知識カードを-corepolicy-に続く第-3-の知識源として-db-化する手法カードの再設計)「規律は CORE へ吸収」に沿う）＝「残す価値のある非自明な知識が出たら、要点を要約し『この内容で知識ノートにしていい？』と一言確認し、合意してから `propose_card` で起票。乱発しない。一般教科書知識はカードにしない。有効化は人間が `/cards` で行う」。
  - **チャット内のカード起票フィードバック**＝`ChatResponse` に `card_ids` を追加（`journal_id` と同型）。`persist_card_ops_from_tool_runs` が既に返す `cards` を router が拾って載せ、frontend が直近ターンの起票を `/cards` 導線付きでインライン表示する。
- **理由**: 知識ノートの土台（[ADR-062](#adr-062-知識カードを-corepolicy-に続く第-3-の知識源として-db-化する手法カードの再設計)）は完成しており、欲しいのは UX（壁打ち体験＋大画面）。データモデルも承認も既に統一されているため一本化の技術的動機は無く、入口 2 つは役割が違うので両方残すのが素直。OPEN-I は「フローティング常駐と専用ページの二重維持コスト回避」が理由だったが、Context 共有で会話を一本化できれば二重維持にならず、URL を持つ大画面の価値（ブックマーク・広い入力域）が上回る。壁打ち規律は毎回効かせたい普遍規律ゆえ CORE（常時注入・[ADR-015](#adr-015-システムプロンプトは不変-core可変-policy-に分離する)）に置き、知識カードにはしない（[ADR-063](#adr-063-core-論証規律の強化業績の質シグナル-family外部スキル集-financial-services-の蒸留) の論証規律と同じ判断）。`card_ids` は `journal_id` の既存パターンの素直な拡張で、副作用経路（W2・`begin()` 内 persist）は不変。
- **代替案**: **フローティングに「最大化」トグルを足すだけ（ルート無し）**→ URL を持てず「画面」感に欠ける・OPEN-I 温存はできるが要望の核（大画面ページ）に応えない＝却下。**`/advisor` を独立会話にする**→ 会話が 2 つ併存して混乱＝却下。**カード作成をアドバイザー経由に一本化（直接フォーム廃止）**→ 外部メモを貼って即ファイルする用途が重くなり `propose_card` のツール呼び出し確実性に依存＝却下。**壁打ち規律を Tool description だけに書く／knowledge_cards に入れる**→ 面横断しない・検索ヒット時しか出ない＝[ADR-062](#adr-062-知識カードを-corepolicy-に続く第-3-の知識源として-db-化する手法カードの再設計)/[ADR-063](#adr-063-core-論証規律の強化業績の質シグナル-family外部スキル集-financial-services-の蒸留) に倣い CORE へ。
- **段階**: 実装済み（2026-06-30・ATDD）。backend＝`advisor/router.py` の `ChatResponse.card_ids`＋persist 戻り値の中継・`advisor/core_prompt.md` 要素④に壁打ち規律・`tests/test_card_chat_ops.py` に `/chat`→`card_ids` の結合テスト 2 件（pytest/ruff green）。frontend＝`lib/advisor-chat-context.tsx`（Provider・会話状態/localStorage/送信/`lastCardIds`）・`components/advisor/ChatConversation.tsx`（共用会話本体・紹介文に知識ノート追加を明記・起票フィードバック）・`components/advisor/AdvisorChat.tsx`（フローティング枠にリファクタ・`/advisor` で非表示）・`app/advisor/page.tsx`（大画面）・`app/layout.tsx`（Provider 設置）・`lib/nav.ts`（`/advisor` へ遷移）・`components/shell/Sidebar.tsx`（action 分岐撤去）・`lib/api/advisor.ts`（`card_ids` 型）。Biome/tsc green。migration 不要。
- **追補（2026-07-02・ドシエ vs 知識ノートの置き場所逆提案）**: 銘柄スコープの知識ノート（ADR-062 追補）で「銘柄に紐づくテキスト」の置き場所がドシエ（今の事実・現況＝`investigate_stock` が毎晩上書き）と知識ノート（耐久的な解釈・アノマリー＝蓄積・承認制）の 2 つになった。そこで壁打ち作法に**置き場所の逆提案**を含める＝ユーザーがどちらかに入れようとして内容が明らかに逆向きなら、AI が**書く前に理由を添えて正しい置き場所を提案**する（両方向＝ノート依頼が"今の事実"なら `investigate_stock` を／調査中に"耐久的知見"が出たら `propose_card` を・**明らかに逆のときだけの弱ナッジ**で乱発しない・`investigate_stock` はコストゆえ勝手に走らせず確認）。実装は**プロンプトのみ**＝CORE 要素④に 1 ボレット＋`propose_card`/`investigate_stock` の tool description に相互参照、handler 無改変（置き場所判定はテキストの意味の読み取り＝LLM の解釈に委ねる＝ADR-014）。docs 同期＝advisor.md。
- **関連**: [ADR-024](#adr-024-ai-advisor-チャットを全ページ常駐にするフローティング)（常駐チャット＝補強）・[ADR-062](#adr-062-知識カードを-corepolicy-に続く第-3-の知識源として-db-化する手法カードの再設計)（知識カード・`propose_card`）・[ADR-009](#adr-009-提案承認制発注はしない決めるのはユーザー)（承認制・active 化は人間）・[ADR-029](#adr-029-昼チャットの会話は揮発localstorage重要点は承認付きで-journal-に昇格)（会話揮発・`journal_id` の手本）・[ADR-025](#adr-025-画面コンテキスト注入は軽量ヒントのみ数値は渡さない)（画面コンテキスト）・[ADR-015](#adr-015-システムプロンプトは不変-core可変-policy-に分離する)（CORE は規律の置き場）・[ADR-014](#adr-014-ai-に数値を計算させないpython-が事実を計算する)（事実は Tool）・[screens.md](screens.md)・[advisor.md](advisor.md)・[api.md](api.md)。

---

## ADR-066: AI Alpha Scorer の学習は「別 PC＝開発機のコンテナ内」で現用 DB を読み取り専用で回す（make 起動・ADR-006 の運用具体化）

- **背景**: Phase 5（AI Alpha Scorer）の初回学習を実機で回す段で 3 つの障害が判明した。① 学習データ（現用 dev DB）は [ADR-060](#adr-060-dev-の-sqlite-を-named-volume-に載せる-bind-mount-で-wal-が壊れた) で **named volume `assetvane-db`** に載っておりホストから素ファイルとして読めない。当初は `make db-backup`（VACUUM スナップショット）で吸い出して学習したが、毎回取り出すのは無駄で現用と乖離する。② backend イメージに **`libgomp1`（LightGBM の OpenMP ランタイム）が無く `import lightgbm` が `OSError: libgomp.so.1` で落ちた**。③ 学習ラベル「対 TOPIX 超過リターン」の **TOPIX 指数（`^TPX`）が Free プランで取れない**（J-Quants Light 以上・Yahoo/Stooq に有効シンボル無し＝[ADR-040](#adr-040-topix-は-jquants-の-indices-から取得する-stooqyahoo-にない)）。
- **決定**: [ADR-006](#adr-006-重い処理の置き場所学習は別-pcmcp-は昼のみ) の「学習は別 PC」を**開発機（Mac）の Docker コンテナ**として具体化する。
  1. **`make train-ai-alpha`**（= `docker compose run --rm --no-deps backend uv run python -m app.scripts.train_ai_alpha`）で一時コンテナを起こし、**現用 DB（named volume）を `?mode=ro` で直読**する（[ADR-002](#adr-002-sqlite--wal--db-に触るのは-fastapi-1-プロセスだけ) の書きロック競合なし＝学習は SELECT のみ）。`.pkl`＋メタは `./models`（bind mount でホスト `backend/models/`）へ。バックアップ吸い出しは不要。
  2. **学習専用の別 `.venv` は作らない**。`lightgbm`/`pandas`/`joblib` は推論用に既にイメージへ入っており、回帰学習で増える追加依存は無い（分類 AUC の `scikit-learn` は回帰では不要）。
  3. **`libgomp1` を Dockerfile の base に追加**する。dev の学習と **prod の推論（`score_ai_alpha` が `.pkl` を load→predict）の双方で要る**（`.pkl` 配置後に prod でも `import lightgbm` するため＝潜在バグの根本修正）。
  4. **ベンチ TOPIX は Free では TOPIX 連動 ETF `1306.T` をプロキシ**にする。`make backfill-topix`（`app.scripts.backfill_topix_benchmark`＝IndexAdapter の Yahoo 恒等取得）で `index_quotes` に `symbol='1306.T'` を投入し、学習は `--bench-symbol 1306.T` で参照（既定は `^TPX`）。Light 以上にしたら本物の `^TPX` に戻せる。
  5. 学習コードに **`walk_forward_cv`**（時系列 expanding-window CV・リーク防止）と `build_training_set(with_dates=)` を追加し、CLI が CV→fit→save を一括で回す。
- **理由**: 現用 volume を直読すればバックアップ往復という無駄が消え、読み取り専用なら [ADR-002](#adr-002-sqlite--wal--db-に触るのは-fastapi-1-プロセスだけ) を破らない。学習依存は既にイメージにあるため別 .venv は過剰。`libgomp1` は推論にも要るので base に置くのが正しい層。ETF プロキシは総リターン連動で相対超過リターンのベンチとして妥当（[ADR-010](#adr-010-データソースはアダプタ越しにする) のアダプタ越し取得を流用）。
- **代替案**: **毎回バックアップ吸い出し**→現用と乖離・無駄＝却下。**学習専用 .venv/サービス（profiles）**→依存は既にあり過剰（将来 optuna 等を入れるなら uv extra group `train` を後付け）＝却下。**`^TPX` を Light で取る**→Free 運用の前提に反する将来課題＝保留。**学習を CLI 化せず手書きスクリプト**→再現性・make 一発の運用性に劣る＝却下。
- **段階**: 実装済み（2026-06-30・ATDD）。`quant/ml/train.py`（`walk_forward_cv`＋`with_dates`・テスト 4 件）・`app/scripts/train_ai_alpha.py`・`app/scripts/backfill_topix_benchmark.py`・`Makefile`（`backfill-topix`/`train-ai-alpha`）・`backend/Dockerfile`（`libgomp1`）。**初回学習を完走**（サンプル 22,219・horizon=60・walk-forward CV RMSE 0.2316±0.040／IC 0.0814±0.067）し `ai_alpha-2026-06-30.pkl` を配置・`load_active` で feature_names 一致を確認。実測は [ml-training.md](ml-training.md) の `【実測】` 欄。**運用注記（2026-07-01・ユーザー協議）**: この Mac コンテナ学習は初回ベースラインで、**運用を Mac 学習だけに固定しない**——大規模データや GPU が効くハイパラ探索などスペックが要る再学習は GPU 搭載ゲーミングPC で回す余地を残す（[ADR-006](#adr-006-重い処理の置き場所学習は別-pcmcp-は昼のみ) の「学習は別 PC」の精神を維持し、Mac コンテナはその一手段）。残＝本番ラズパイへの `.pkl` rsync 配布・ハイパラ調律。
- **関連**: [ADR-006](#adr-006-重い処理の置き場所学習は別-pcmcp-は昼のみ)（学習は別 PC＝本 ADR が具体化）・[ADR-002](#adr-002-sqlite--wal--db-に触るのは-fastapi-1-プロセスだけ)（読み取り専用でロック回避）・[ADR-060](#adr-060-dev-の-sqlite-を-named-volume-に載せる-bind-mount-で-wal-が壊れた)（named volume）・[ADR-021](#adr-021-開発も本番も-docker-compose-で動かす)（Docker Compose）・[ADR-040](#adr-040-topix-は-jquants-の-indices-から取得する-stooqyahoo-にない)（TOPIX 取得制約）・[ADR-010](#adr-010-データソースはアダプタ越しにする)（アダプタ越し）・[ADR-016](#adr-016-手法はテスト済みコードで実装する-ta-lib-は使わない)（CV は再現性）・[ml-training.md](ml-training.md)・[roadmap.md](roadmap.md) Phase 5。

---

## ADR-067: 夜 digest の「注目シグナル」を合流(confluence)ゲート＋AI 選別に作り直す（注目過多の解消）

（2026-06-30）

- **状況**: 夜バッチ digest の「注目シグナル」が実機で **2642 signals 中 1294 件**も notable になり、機能していなかった。根は ① `quant/momentum.py` の `notable = golden_cross or score>=0.6`（momentum.py:127）で「上昇トレンド継続＝即 notable」、かつ score が `TREND_BAND=0.05` で +5% gap 飽和・rsi≥70 飽和して強気相場で 1.00 を量産する（lead_lag も 1.00 飽和）。② `notify_digest` は `list_signals_for_alert` の **score 降順 Top10 のみ**表示で、score 1.00 の山にゴールデンクロスや出来高急増の希少イベントが埋もれ**既に表示できていない**（皮肉にも「見落とし」が発生済み）。③ 夜の分析AI も `handle_get_signals({})`→`repo.get_signals(limit=100, score 降順, 全 type 混在)`（stocks.py:197）で score 1.00 の見分けの付かない 100 行を食い、これが digest「AI 提案 なし」の有力な真因だった。`golden_cross` を単独で notable 扱いする設計が過多の発生源（GC は遅行・ありふれ・単独では騙しが多い）。
- **決定**:
  - **触る層は通知/夜AI 連携で、`quant` の score 勾配は温存**（[ADR-026](#adr-026-signals-は連続スコアの材料ai-が主消費者で閾値は破壊的ゲートにしない) の「near-miss を濃淡で残す」資産＝backtest/AI 入力を壊さない）。digest の「score 閾値 Top N」notable 抽出は廃する。
  - **候補集合を「合流(confluence)ゲート」で作る**。Python が独立した材料次元を判定し、**広い母集団は材料2次元以上が重なった銘柄だけ**を候補にする（GC 単独・トレンド継続単独は候補から落ち、`/signals` の DB 記録にのみ残す＝安全網）。材料次元（独立4つ・**相関は1つに数える**＝GC と RSI 反転は両方点いても「値動き」1個）＝ **①値動き（当日大幅変動 ±X%〔例 ±7%〕 or ゴールデンクロス or RSI 反転）／②出来高急増（volume_spike ratio 高）／③ニュース（直近 24h の polarity 付き stock 層ニュース・[ADR-049](#adr-049-ニュース-rag-の活用を線引きするai-は事実を解釈するだけで数値スコアは作らない)）／④リードラグ（当日 lead_lag リーダー）**。ai_alpha は `.pkl` 配置後に⑤として自動参入。
  - **carve-out**: 広い母集団でも **出来高極増（ratio≥高閾値〔例 7 倍〕）は単独で候補**（ニュース未取得の異常検知）。**レーダー枠（保有 holdings ∪ ウォッチリスト watchlist）は材料1次元で候補**（GC 単独でも自分が既に気にしている銘柄ゆえ拾う・材料ゼロは出さず静かな日は黙る）。
  - **候補は AI に渡し、AI が総合選別する**（[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け)＝Python は事実〔点いた材料＋値のタグ〕を作り、AI は解釈・選別・説明）。**入力**＝Python が候補集合を決定論的に作り**夜AI プロンプトに直接注入**（ツール呼び出し依存なしで堅牢＝[ADR-018](#adr-018-無人運用の障害時方針失敗を黙って放置しない)）＋同 service を `get_notable_candidates` Tool でも公開し昼チャット（[ADR-024](#adr-024-ai-advisor-チャットを全ページ常駐にするフローティング)）も同経路で救済。**出力**＝新 Tool **`submit_notable_stocks(picks=[{code, reason}])`** で受け、**新テーブル `notable_picks`** に `persist_notable_picks_from_tool_runs`（begin() 内・`persist_trade_proposals` と同型＝[ADR-052](#adr-052-ニュース起点の売買アイデアは-proposals-の-buysell-に承認制で起票する)）で永続。digest が後で読む。
  - **digest 本文は【決定論: 保有の悪材料（[ADR-051](#adr-051-ニュースとシグナルと保有を結ぶ能動配信を-notify_digest-に拡張する) 維持＝AI の拾い忘れでも必ず出す安全装置）】＋【AI 選別の注目（notable_picks）】＋【極薄サマリ 1 行（signals N／イベント M／候補 K／AI 選別 P・溢れたドロップ件数も）】**。旧 `_is_alert`/Top10 抽出は撤去。
  - **つまみは慣例踏襲**（[ADR-016](#adr-016-手法はコードで実装する手法db-は索引でありコードの代替ではない)/[ADR-027](#adr-027-手法パラメータは-phase-1-はコード定数将来-method_settings-ai-は助言自動改変しない)）＝手法閾値（大幅変動 %・出来高極増 ratio・合流に要る次元数）は新 quant/service モジュールの定数（再現性・テスト同居）、運用つまみ（候補総数バックストップ上限・digest 表示数）は `config.settings`。DB+WebUI 化（method_settings）は将来。
- **理由**: 「GC だから注目」は GC が遅行・高頻度・単独では騙しが多いため過多の発生源そのもの。プロも単独シグナルより独立材料の重なり（confirmation）を重視する。合流ゲートにすると候補が自然に少数・高品質化し、**score 1.00 飽和が候補集合に効かなくなる**（合流は score を使わず GC/RSI 反転/大幅変動・ratio の事実で判定）ため、[ADR-026](#adr-026-signals-は連続スコアの材料ai-が主消費者で閾値は破壊的ゲートにしない) の score 勾配を温存したまま過多が根元から消える。材料①に「当日大幅変動」を足すのは、GC/RSI 反転だけだとギャップアップ（最も強い『何か起きてる』サイン）を取りこぼすため（`daily_quotes` 前日比の軽い純関数で足りる）。選別を AI に委ねるのは [ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け) に素直だが、「ただ全 signals を渡す」は今と同じ 1.00 の壁で死ぬので、**Python が多様な候補集合を作って渡すのが肝**。入力をプロンプト注入にするのは弱モデル依存を避け確実性を上げるため（[ADR-012](#adr-012-llm-はアダプタで抽象化openrouter-既定ローカルへ差替可)/[ADR-018](#adr-018-無人運用の障害時方針失敗を黙って放置しない)）。保有の悪材料だけ AI 判断に委ねず決定論で残すのは [ADR-051](#adr-051-ニュースとシグナルと保有を結ぶ能動配信を-notify_digest-に拡張する) の安全配信の保証を崩さないため。
- **代替案**: **`quant/momentum.py` の閾値/重みを直す（D: score 飽和の解像度）**→ backtest/AI 入力の意味が変わり [ADR-026](#adr-026-signals-は連続スコアの材料ai-が主消費者で閾値は破壊的ゲートにしない) を割る・本質は通知層の選別なので今回は据え置き（`/signals` 並び順への影響のみ残る・将来課題）。**イベントを生で羅列**→ GC 過多がそのまま残る＝却下。**全 signals を AI にそのまま渡す**→ score 1.00 の見分け付かない 100 行で選別不能（現状の真因）＝却下。**`submit_journal` を拡張して picks を持たせる**→ 観測/方針と注目選別は別概念で submit_journal が肥大＝専用 Tool に分離。**保有の悪材料も AI 選別に委ねる**→ 拾い忘れで安全アラートが消える＝決定論セクションで残す。**つまみを最初から DB+WebUI 化**→ migration+router+UI でスコープが広がる・手法閾値はコード同居が [ADR-016](#adr-016-手法はコードで実装する手法db-は索引でありコードの代替ではない) 的に筋＝将来。
- **段階**: 実装済み（2026-07-01・ATDD＝受け入れテスト先行）。着手物＝① quant 純関数（当日大幅変動）② services 候補ビルダー（合流ゲート＋carve-out＋タグ付け）③ `notable_picks` テーブル＋migration `0032`（最新 0031）④ Tool `submit_notable_stocks`/`get_notable_candidates` 登録＋handler ⑤ `persist_notable_picks_from_tool_runs` ⑥ `advisor/nightly.py` の候補注入改修 ⑦ `batch/jobs/notify_digest.py` 書き換え（決定論 保有悪材料＋notable_picks＋極薄サマリ）⑧ テスト ⑨ ADR-067＋[phase6-spec.md](phase-specs/phase6-spec.md)＋CLAUDE.md 同期。**検証（2026-07-01）**＝dev Docker で `notify_digest.run()` 経路（`build_digest_content`→`send_once`）の Discord 到達＋冪等（2 回目は既送 skip で二重送信なし）＋合流ゲート（signals 2887→候補 116 に収束）を実機確認済み（Discord 目視到達）。ただし AI 選別（`notable_picks`）込みの本文とラズパイ cron 経由の到達は次回運用時に確認（当該 dev DB は 6/15 以降 cron 停止で `notable_picks` 未生成のためサマリ中心の digest だった）。流用＝`notify_digest._holding_risk_lines`（[ADR-051](#adr-051-ニュースとシグナルと保有を結ぶ能動配信を-notify_digest-に拡張する)）・`repo.list_holding_codes`/`list_negative_stock_news_for_codes`・watchlist（Phase 4）・lead_lag/news repo。
- **関連**: [ADR-026](#adr-026-signals-は連続スコアの材料ai-が主消費者で閾値は破壊的ゲートにしない)（score 勾配＝温存）・[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け)（事実は Python・選別/解釈は AI）・[ADR-016](#adr-016-手法はコードで実装する手法db-は索引でありコードの代替ではない)（手法はコード・つまみは定数）・[ADR-018](#adr-018-無人運用の障害時方針失敗を黙って放置しない)（注入で堅牢・障害時方針）・[ADR-051](#adr-051-ニュースとシグナルと保有を結ぶ能動配信を-notify_digest-に拡張する)（保有の悪材料＝決定論で維持）・[ADR-049](#adr-049-ニュース-rag-の活用を線引きするai-は事実を解釈するだけで数値スコアは作らない)（ニュース polarity）・[ADR-052](#adr-052-ニュース起点の売買アイデアは-proposals-の-buysell-に承認制で起票する)（`persist_*_from_tool_runs` の手本）・[ADR-024](#adr-024-ai-advisor-チャットを全ページ常駐にするフローティング)（常駐チャット救済）・[ADR-012](#adr-012-llm-はアダプタで抽象化openrouter-既定ローカルへ差替可)（強モデル前提）・[ADR-006](#adr-006-機械学習の学習は別-pcラズパイは推論のみ)（ai_alpha は配置後参入）・[phase6-spec.md](phase-specs/phase6-spec.md)・[data-model.md](data-model.md)・[api.md](api.md)・[advisor.md](advisor.md)。

## ADR-070: バッチ停止フラグをファイル化する（メモリ singleton の停止を `data/batch.stop` へ・ADR-036 改訂）

（2026-07-01）

- **状況**: 夜間バッチの停止（[ADR-036](#adr-036-バッチは停止できる状態が見える実行状態はメモリ-singleton停止は協調キャンセル) の協調キャンセル）が dev で効かないと実機で判明した（`fetch_quotes` が止まらない）。真因は「粒度」ではなく「**クロスプロセス**」だった。① バッチは `POST /batch/run` の BackgroundTask・APScheduler・CLI `--nightly` のいずれも走るが、停止フラグ `stop_requested` は **FastAPI プロセス内のメモリ singleton**（ADR-036）に持っていた。② dev は uvicorn `--reload`。ソース編集や（意図せぬ）`uv run`〔bytecode 8481 本を pre-compile する〕でリロードが走ると、走行中バッチは**古いプロセスに取り残されたまま**（uvicorn が in-flight リクエストの完了を待つので即 kill されない）、`POST /batch/stop` は**新しい前面プロセスのメモリ**に旗を立てる＝走行中バッチに一生届かない。`uv run` 連打＝リロード連打でバッチは API から実質不可侵になった。③ CLI 起動（`backfill --nightly`）は別プロセスなのでメモリ旗ではそもそも最初から届かない。相互排他はもう `batch.lock` の `fcntl.flock`＝**クロスプロセス**なのに、停止（キャンセル）だけメモリに閉じていた**非対称**が根。
- **決定**:
  - **停止フラグをファイル `data/batch.stop`（`batch.lock` の兄弟）に出す**（ADR-036 の「実行状態はメモリ singleton」のうち**停止の保存方式だけ** supersede）。`should_stop()` は `batch.stop` の存在を見る（真実源）＝reload/編集/CLI のどれで別プロセスに分裂しても走行中バッチに届く。
  - **`request_stop()` の running ゲートを撤廃**し、常にファイルを touch して True を返す（前面プロセスの `running=false` でも受理する）。idle 中の stray な要求は次の `begin()` が回収する。
  - **ライフサイクルの不変条件**＝停止ファイルの生成（touch）はロック外の `request_stop()` から・消去（unlink）は **flock 保持中の `begin()`/`end()` だけ**（runner の `with lock.acquire()` 内）。これで「走行中バッチには必ず届く／idle の取りこぼしは次 begin が回収」を両立し、起動時クリアはしない（orphan 宛の停止要求を誤消去しないため）。
  - **status（running / current_job / …）はメモリ据え置き**（best-effort）。帰結＝reload-orphan 中は前面プロセスの `running=false` で **UI は停止ボタンを出さない**が、その時は直 API（urllib で `POST /batch/stop`）で止める（既存運用と同じ）。`snapshot()` の `stop_requested` だけはファイルを見て真実を返す。
  - **粒度＝`state.stop_aware(iterable)` ヘルパを新設**し、長尺ジョブの最内ループを一律に包む（ADR-036 追補「長尺ジョブは内部ループでも should_stop」の一般化）。ジョブ境界停止だけだと「3〜4 時間の `fetch_quotes` は 1 ジョブ＝止まらない」ため、最内ループで見る。既存 5 本（`fetch_quotes`/`fetch_us_quotes`/`fetch_us_fundamentals`/`calc_receivables_inventory`/`calc_us_receivables_inventory`）を統一し、`investigate_dossier`/`embed_news`/`embed_themes`/`embed_cards`/`tag_news_polarity`/`tag_jp_themes`/`tag_us_themes`/`fetch_edinet_descriptions`/`fetch_index`/`fetch_financials`/`fetch_fund_navs` にも足す。**cap 付き LLM/embed 系にも足す**のは、helper 化で 1 行になりコストがほぼゼロになったため（batch-pattern スキルの「cap で短いものには足さない＝過剰」判断を改訂）。`fetch_edinet_descriptions` は共有 `crawl()` の**提出日境界**で見る（日末に fetch_meta を前進させる作りなので日単位で止めればカーソルがクリーン・日の途中で止めると未要約 doc を落とす）。
  - **reload-orphan（バッチ中の編集/uv run で走行中バッチが古プロセスに残る）は機構ゼロ・運用規律のみ**＝「バッチ中に編集したければ stop（今は効く）→編集→再開」を doc/スキルに注記。orphan 自体は named volume＋WAL で multi-process 書き込みが安全（[ADR-060](#adr-060-dev-の-sqlite-は-named-volume-に載せるmacos-docker-desktop-の-bind-mount-では-walmmap-が壊れるためprod-は-bind-mount-維持) の破損は FUSE が主因）。file-flag は**リロードの引き金を問わず**効くので、引き金（`uv run` の bytecode 説は uvicorn が `*.py` しか見ない点と矛盾し未確定）を完全特定しなくてよい。
- **理由**: 相互排他（flock）が既にクロスプロセスなら、キャンセルも同じ土俵に置くのが素直（stdlib・migration 不要・UPSERT 冪等での再開も維持）。DB 行にしないのは、[ADR-005](#adr-005-db-に触れるのは-fastapi-のみnext-は-rest-経由) の書き手一本化は保てるがホットパスの DB I/O とロック lifecycle が増えるため＝ファイルが最軽量。status をメモリのままにするのは、rich な current_job 等はプロセスが死ねば消えて整合する best-effort で十分で、cross-process 化（flock 探り/marker）は lock 競合や stale の考慮が増えるため。stop_aware を cap 系にも一律で被せるのは、粒度の要件「長い 1 ジョブはジョブ境界で止められない」をジョブ横断で満たすためで、helper 化がその一律適用のコストを消した。
- **代替案**: **DB に停止行を持つ**→ホットパスの read/write＋migration＝ファイルより重い＝却下。**status も cross-process 化（flock 探り or marker file）**→ 探りが実 acquire と競合して spurious 409／marker は crash で stale＝best-effort メモリで足りる＝据え置き。**reload 抑制（reload-exclude 追加・polling 見直し）で orphan を断つ**→ 引き金の完全特定が要りフレームワークと戦う・file-flag は引き金非依存で効くので不要＝却下。**batch を別プロセス/サービス化（--reload 配下から出す）**→ orphan 概念が消える根治だが大改修＝別途判断（runner docstring の「将来の専用 batch サービス」）。**workaround（uv run を叩かない）だけで運用**→ 編集厳禁・CLI 不能が残る＝恒久対処にならない。
- **段階**: 実装済み（2026-07-01・ATDD＝受け入れテスト先行）。`state.py`（file 化＋`stop_aware`）・受け入れテスト（`test_batch_state`/`test_batch_stop`＝停止ファイルを別プロセス相当で直に touch するクロスプロセス代理テスト含む）・16 ジョブへ `stop_aware` 適用・[batch-pattern] スキルと CLAUDE.md を同期。pytest/ruff/pyright green（pyright の既存 4 件〔`fetch_edinet_descriptions` の `dict[str, object]` 由来〕は本変更と無関係で据え置き）。**実機の夜バッチで stop が効くこと・digest への影響は次回運用時に確認**（migration なし）。
- **観測ギャップの受け入れ（2026-07-01・全体レビュー #19）**: 「stop はファイル（クロスプロセス）だが status（running/current_job）はメモリ（best-effort）」の非対称を確認し、**status のファイル化はしない方針を維持**（選択肢 A＝受け入れ）。CLI `--nightly` や dev `--reload` の別プロセスの走行は `GET /batch/status` に映らないが、① flock（`data/batch.lock`）が二重起動を防ぐので実害なし ② 観測/画面停止したい重いバッチ（初回フル含む）は `POST /batch/run` 裏タスク（FastAPI 同一プロセス）で回せば status/stop 両方が効く、を運用指針として [deploy.md](deploy.md) トラブルシュートに明記。status のファイル化（選択肢 C）は低頻度の観測ズレのためにこの判断を蒸し返す割にコスト/flock race リスクが見合わず却下。
- **関連**: [ADR-036](#adr-036-バッチは停止できる状態が見える実行状態はメモリ-singleton停止は協調キャンセル)（この停止機構の親・保存方式のみ改訂）・[ADR-005](#adr-005-db-に触れるのは-fastapi-のみnext-は-rest-経由)（単一書き手・DB は FastAPI）・[ADR-018](#adr-018-無人運用の障害時方針失敗を黙って放置しない)（停止＝正常終了で通知しない）・[ADR-060](#adr-060-dev-の-sqlite-は-named-volume-に載せるmacos-docker-desktop-の-bind-mount-では-walmmap-が壊れるためprod-は-bind-mount-維持)（named volume・WAL で multi-process 安全）・batch-pattern スキル。

## ADR-071: is_delayed を「プランの仮定」から「as_of の鮮度実測」に一元化する（AI は Tool の遅延フラグを見る）

（2026-07-02）

- **状況**: AI Advisor チャットが任天堂の相談で「※株価データは AssetVane 上で遅延フラグあり」と誤って注記した。実環境は J-Quants **light プラン**（`plan_status().delay_days=0`＝遅延なし）で、事実として遅延はなかった。真因は 2 つ。① advisor tool handler が `_IS_DELAYED = True` のハードコード（[ADR-008](#adr-008-j-quants-は-free-プランで開発運用時に有料へv2-を使う) の「Free は 12 週遅延」の慣習をコードに焼いた）で、全 price 系 Tool（get_indicators／get_valuation／get_portfolio_metrics／get_asset_overview／get_us_valuation 等）が実プラン・鮮度に関係なく `is_delayed=True` を返していた。REST ルータ（portfolio／assets）も `is_delayed=True`・`plan="free"` 固定で同じ嘘をついていた（[ADR-061](#adr-061-j-quants-の-api-キーとプランを-env-から-dbwebuisettings-へ移管する) は右上バッジだけ `plan_status` 由来に直し、AI／画面の per-response フラグは取り残された**ドリフト**）。② CORE プロンプト（core_prompt.md）が「株価・評価額は Free プランで約 12 週間遅延しうる」と**無条件に断定**し、AI はこのガードレールに引きずられて Tool 戻り値の `is_delayed` を見ずに「遅延あり」と述べた（本人も「Tool 戻り値でなく上位ルール由来」と自白）。
- **決定**:
  - **is_delayed はプランの仮定でなく「as_of の鮮度」で判定する**＝共有 pure 関数 `services/freshness.is_delayed(as_of, today=None, threshold_days=7)` を新設。`(today - as_of) >= 7 日（暦日）` で True。`as_of` が None／parse 不能（データ無・未取得）は鮮度を確認できないため保守的に True。DB を知らない純関数で、routers と advisor tool handlers の両方から使う。
  - **プランは読まない**。J-Quants Free は物理的に約 84 日遅れのデータしか配信しないので DB の最新 daily_quotes 行の日付（as_of）自体が古く、鮮度だけで Free 遅延が自動的に True になる。同じ判定で「有料プランだが夜間バッチ停止で古い（stale）」も捕まえる（プランベースでは見逃す）。US（yfinance）はプラン概念が無いが同じ鮮度判定で stale を捕まえる（市場非依存＝JP／US 共用）。
  - **重複の一元化**＝従来の `_IS_DELAYED=True`・handler の `_signals_is_delayed`・routers の signals／portfolio／assets の各ローカル `_is_delayed`／True 固定を `freshness.is_delayed` へ集約。signals の「空＝当日発火なしは遅延ではない」意味論は呼び出し側で False に倒して保持（価格系の None→True とは別扱い）。lead_lag ルータの独自 30 日境界（プランハイブリッド）は月次データ向けの意図的な別値なので対象外。
  - **quant からは is_delayed を撤去**＝`compute_portfolio_metrics`／`optimize_portfolio`／`backtest_portfolio` が返していた `is_delayed=True` を削除し `as_of` だけ返す。遅延は today 依存＝再現性・backtest を壊す（[ADR-016](#adr-016-手法はコードで実装する手法db-は索引でありコードの代替ではない)）ので純関数が持つべきでない。消費側（handler／router）が `as_of` から算出する。
  - **REST ルータの plan も実プラン中継**＝portfolio／assets の `plan="free"` 固定を `current_plan(conn)`（[ADR-061](#adr-061-j-quants-の-api-キーとプランを-env-から-dbwebuisettings-へ移管する) の `plan_status` 同源）に。`is_delayed=False` なのに `plan="free"` という不整合レスポンスを防ぐ。
  - **CORE プロンプト改訂**＝「Free で 12 週遅延しうる」の無条件断定を撤回し、「データ鮮度は Tool 戻り値の is_delayed／as_of で判断する。is_delayed が true のときだけ注記し、false／未取得なら遅延と断定しない。プランや一般論から遅延を推測しない」に置換。
- **理由**: 遅延の真実は「いま見ているデータが実際どれだけ古いか」であって契約プランの仮定ではない。鮮度実測は Free の構造遅延と stale batch の両方を 1 つの規律で捕まえ、有料プラン・新鮮データでは正しく「遅延なし」になる。プランを読まない一本化で JP／US・全 Tool・全画面が同じ規律で一致する。新ツールを足さないのは、`is_delayed` が既に全 price 系 Tool の戻り値に載っており（冗長）、専用ツールは AI の呼び忘れ経路を増やすため（[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け) の「事実は Tool 戻り値で受け取る」を素直に使う）。
- **代替案**: **プランベース（free→True／有料→False）**→有料でバッチ停止の stale を見逃す＝却下。**プラン OR 鮮度のハイブリッド**→free は鮮度でも必ず True になりプラン短絡が実質冗長＝鮮度一本に吸収。**専用の遅延判定 Tool（bool 返し）を新設**→既存 `is_delayed` と二重管理・AI の呼び忘れ経路が増える＝却下。**quant に is_delayed を残す**→today 依存の値を純関数が持つ [ADR-016](#adr-016-手法はコードで実装する手法db-は索引でありコードの代替ではない) 違反・常に上書きされる dead な嘘＝撤去。
- **段階**: 実装済み（2026-07-02・ATDD＝受け入れテスト先行）。`services/freshness.py`＋`test_freshness`（None／parse 不能／当日／6 日／7 日／84 日／閾値上書きを today 注入で決定的に検証）・handler／router 配線・quant からの is_delayed 撤去・get_indicators の fresh/old 反転テスト・`/asset-overview` の fresh→False テスト・CORE 改訂を含む。pytest／ruff／pyright green。**実機の任天堂チャット再現（light プラン・新鮮データで「遅延あり」注記が出ないこと）は次回運用時に確認**（migration なし）。
- **関連**: [ADR-008](#adr-008-j-quants-は-free-プランで開発運用時に有料へv2-を使う)（プラン別の株価遅延・この「True 固定」慣習を supersede）・[ADR-061](#adr-061-j-quants-の-api-キーとプランを-env-から-dbwebuisettings-へ移管する)（右上バッジを `plan_status` 由来に＝バッジだけ直したドリフトを AI／画面まで完結）・[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け)（事実は Tool 戻り値で受け取る）・[ADR-016](#adr-016-手法はコードで実装する手法db-は索引でありコードの代替ではない)（quant は today を知らない純関数）・[ADR-018](#adr-018-無人運用の障害時方針失敗を黙って放置しない)（未設定／障害時のフォールバック）・core_prompt.md。

## ADR-072: AI Advisor チャットを送信中にキャンセルして編集・送り直しできるようにする（クライアント abort ＋サーバ側 is_disconnected 打ち切り）

（2026-07-02）

- **状況**: `POST /chat` は非ストリーミングの一括往復で、送信すると frontend は `busy` 中に入力欄と送信ボタンを disabled にするだけ＝**進行中の fetch を止める手段が UI にもロジックにも無かった**（`advisor-chat-context.tsx` に `AbortController` 参照なし・`_client.ts` の `postJSON` は `signal` を受けず〔`getJSON` は既に受ける非対称〕・backend に `is_disconnected` 監視なし）。ユーザー要望は「**誤送信を拾いたい**＝送信直後に気づいて止めて、文面を直して、送り直す」。加えて「無駄な LLM 消費を止めるためサーバ側も確実に打ち切りたい」。
- **決定**:
  - **送信中はずっと中止できる**（時間窓は設けない）。`busy` 中は送信ボタン（➤）を「■ 中止」に化けさせる単一コントロール（ChatGPT 型）。誤送信も「方向が違う」も同じ 1 ボタンで拾う。
  - **サーバ側も打ち切る**＝`chat` ハンドラに Starlette `Request` を受け、`run_turn`（LLM ループ）を `service.run_turn_cancellable` でタスク化して `request.is_disconnected` を `poll_seconds`（既定 0.25s）ごとに監視する。非ストリーミングで `receive()` を読まないエンドポイントはクライアント切断で**自動キャンセルされない**（`http.disconnect` がキューに積まれるだけ）ため、明示ポーリングが要る。切断検知でタスクを `cancel()` し、`CancelledError` が httpx の in-flight リクエストへ伝播して LLM 呼び出し自体を止める。
  - **DB 書き込み中は打ち切らない（＝ユーザー了承）**。journal / proposals / cards の永続は `chat` 末尾の 1 トランザクションに集約されている。監視は `run_turn` にだけ掛け、`run_turn` 完走後の永続化ブロックはそのまま走らせる（＝永続化は中断しない）。途中で中止すれば永続化に到達しないので**中途半端な起票は残らない**。ループ中の DB 書き込みは `investigate_stock` の dossier UPSERT（冪等キャッシュ・[ADR-020](#adr-020-個別銘柄ドシエ定性ファンダ調査-1銘柄1レポートを更新し続ける)）と `_record_usage` の計上（監査）だけで、中断されても無害。
  - **編集再送 UX**＝中止時に直前に送った user メッセージをスレッドから取り除き、その文面を入力欄に戻す（`cancel()` が abort ＋取り除きを行い、取り除いた文面を返す→`ChatConversation` が `setInput`）。「考え中…」バブルも消える。中止は意図的 abort なので `sendText` の catch は `signal.aborted` を見て⚠バブルを出さない。過去発話の任意編集（会話巻き戻し）は今回スコープ外。会話状態は `AdvisorChatProvider`（Context）にあるので、フローティングと `/advisor` の**どちらから中止しても同一会話に効く**（[ADR-024](#adr-024-ai-advisor-チャットを全ページ常駐にするフローティング)/[ADR-065](#adr-065-ai-advisor-に専用大画面ページ-advisor-を追加し会話状態を共有するopen-i-撤回adr-024-補強知識ノートの壁打ち導線)）。
  - **キャンセルは openai 互換経路だけを想定**し router レベルで汎用実装する（`run_turn_cancellable` は `is_disconnected` を callable で受け service 層に web 依存を持ち込まない）。codex は廃止予定でチャットは openai に統一する方針のため、「面が codex か」を frontend に漏らす配線は作らず（中止ボタンは常時出す）、`codex_engine.py` は無改変（死に経路として特別扱いしない）。SSE 版 `sendChatStream` スタブは据え置き。
- **理由**: 誤送信を拾う UX は frontend の abort が即成立させ、サーバ側 `is_disconnected` レースが「無駄な LLM 消費を止める」を裏で満たす。時間窓を切らないのは、末尾集約設計ゆえ途中中断が安全でタイマー管理が不要な分シンプルなうえ、窓を過ぎた長い/暴走リクエストも拾えるため。`is_disconnected` を callable で受けるのは backend-service-quant-pattern の「service 層に FastAPI 依存を持ち込まない」規律に沿うため。
- **代替案**: **最初の N 秒だけ中止可**→タイマー管理が増え、窓を過ぎた長い応答を止められず、末尾集約で中途半端が起きない以上メリット薄＝却下。**Starlette の自動キャンセルに依存**→非ストリーミングかつ `receive()` 非読取では発火しない定番の落とし穴＝明示ポーリング採用。**codex 面も強制 cancel**→常駐 JSON-RPC シングルトンの要求/応答対応がずれ得るうえ codex は廃止予定＝据え置き。**永続化ブロックも `asyncio.shield` で保護**→監視を `run_turn` 限定にした時点で永続化は監視外＝過剰なので入れない。
- **段階**: 実装済み（2026-07-02・ATDD＝受け入れテスト先行）。`service.run_turn_cancellable`＋受け入れテスト（`test_advisor_service`＝接続維持で結果を返す／切断で None かつ coro が cancel される／例外を再送出の 3 本）・`chat` ハンドラ配線・frontend の `postJSON`/`sendChat` の `signal` 引き回し・`AdvisorChatProvider` の `cancel`・`ChatConversation` の中止ボタン化。backend pytest／ruff／pyright green、frontend biome／`tsc --noEmit` green。**dev サーバでの手動 E2E（中止で発話が入力欄に戻る・中止後に journal/proposals に新規行が増えない・OpenRouter 側で in-flight が続かない）は次回運用時に確認**（migration なし）。
- **関連**: [ADR-024](#adr-024-ai-advisor-チャットを全ページ常駐にするフローティング)（会話は frontend 保持・ステートレスゆえ再送は messages 再 POST で完結）・[ADR-065](#adr-065-ai-advisor-に専用大画面ページ-advisor-を追加し会話状態を共有するopen-i-撤回adr-024-補強知識ノートの壁打ち導線)（会話状態は Context 共有＝どの窓から中止しても効く）・[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け)（失われるのは LLM の解釈テキストのみ・事実は Tool で再取得可）・[ADR-018](#adr-018-無人運用の障害時方針失敗を黙って放置しない)（対話チャットは通知しない・障害は HTTP コードで返す）・[ADR-020](#adr-020-個別銘柄ドシエ定性ファンダ調査-1銘柄1レポートを更新し続ける)（investigate_stock の dossier は冪等キャッシュ）。

## ADR-073: codex 接続の撤去（ADR-032 を Superseded）

- **状況**: [ADR-032](#adr-032-codex-接続は-mcpcodex-app-serverapicodex-を面別切替自動フォールバックなし) で、コスト削減のため AI Advisor の LLM を codex（`codex app-server` を FastAPI プロセス内に常駐させ自前 Tool を `/mcp` 越しに呼ばせる・`/settings` で面に `provider_id=0` として割当）でも動かせるようにした。だが実運用では、(1) ChatGPT サブスクの login トークンは無人 cron での継続に制約があり夜間面（nightly）は結局 openai 推奨で codex を寄せられなかった、(2) app-server 常駐シングルトン＋MCP 経路は openai 経路（`llm.py`/`service.run_tool_loop`）と二系統の障害処理・テスト・JSON 化を抱える保守コスト源で、[ADR-012](#adr-012-llm-はアダプタで抽象化openrouter-既定ローカルへ差替可) の「本番＝クラウド強モデル前提」から見て割に合わなかった。**codex を LLM プロバイダとして使わない、という設計判断をした。**
- **決定**:
  - **codex を LLM プロバイダ経路として全撤去する**。`app/advisor/codex_engine.py`（app-server JSON-RPC シングルトン）と `app/advisor/mcp_server.py`（REGISTRY を codex に渡す MCP サーバ）をファイルごと削除。`engine.py` の provider 分岐（`if face.provider == "codex"`）・`services/llm_config.py` の `CODEX_PROVIDER_ID=0` センチネルと解決分岐・`routers/llm_config.py` の `POST /llm/codex/test`・`main.py` の `mount_mcp`/`session_manager_lifespan`/`codex_engine.shutdown`・`advisor/router.py` の `CodexEngineError` 分岐を除去。`config.py` の `codex_*` 8 フィールド・`.env(.example)` の `CODEX_*`・`Dockerfile` の codex バイナリ同梱（`ARG TARGETARCH`・musl 取得・`curl`）・compose の `.codex` マウントも削除。provider は **OpenAI 互換一本**に戻す（[ADR-012](#adr-012-llm-はアダプタで抽象化openrouter-既定ローカルへ差替可)）。
  - **面の provider_id=0 センチネルを撤去**。`update_face` は `provider_id` を NULL（未設定）か実在 provider の id（>0）のみ許し、0 は 422 で弾く。既存 `llm_face_config` の `provider_id=0` は migration `0034_drop_codex_face_sentinel` で NULL に正規化（DDL 変更なし・挙動は移行前後で不変＝0 も NULL も未設定扱い＝[ADR-018](#adr-018-無人運用の障害時方針失敗を黙って放置しない)）。
  - **reasoning_effort の値域から codex 固有の `xhigh` を外す**（openai は minimal/low/medium/high）。`reasoning_effort` 列自体は openai 面が使うので存続。
  - **開発ツールとしての Codex CLI は撤去対象外**（`.codex/skills` symlink・`AGENTS.md`・設計相談での Codex 利用）。これは開発のやり方であり LLM プロバイダとは別系統。
- **理由**: LLM を本番＝クラウド強モデル前提（ADR-012）に一本化する方が、障害処理・テスト・Tool の JSON 化・起動配線が単純になり、[ADR-005](#adr-005-db-に触れるのは-fastapi-だけ)（DB は FastAPI だけ）/[ADR-014](#adr-014-ai-に数値を計算させない)（AI は計算しない）の規律も openai 経路だけで保てる。codex はコスト削減の後付け経路であり、無人運用の制約で当初の狙い（夜間も codex）を満たせなかった以上、二系統を抱え続ける利が無い。デッドコードを残さない方針にも合う。
- **代替案**: (A) codex 経路を残して「死に経路」として据え置く → 二系統の保守コストとテスト固定（openai 縛り）が残り、デッドコード方針に反する＝却下。(B) 面の provider_id=0 をコードだけ無効化し migration を打たない → ステールなセンチネル 0 がデータに残る＝データ衛生のため migration を採用。
- **影響/段階**: **実装済み（2026-07-02）**。backend（`codex_engine.py`/`mcp_server.py` 削除・分岐/配線/config/env/Dockerfile 撤去・`0034` migration・関連テスト削除/更新）・frontend（`testCodex`・`CodexStatusCard`・provider セレクトの codex option・`xhigh` を撤去）・docs（本 ADR で ADR-032 を Superseded・ADR-058/059 に注記・api.md/data-model.md/architecture.md/deploy.md/README.md/CLAUDE.md から codex を除去）・skills（advisor-tool-pattern/testing-strategy から codex 記述を除去）。backend pytest／変更分の ruff／pyright green、frontend biome／`tsc --noEmit` green。ローカル `backend/.env` の `CODEX_*` は手掃除（`config.py` は `extra="ignore"` なので残っても無害）。
- **関連**: [ADR-032](#adr-032-codex-接続は-mcpcodex-app-serverapicodex-を面別切替自動フォールバックなし)（本 ADR が Superseded）・[ADR-012](#adr-012-llm-はアダプタで抽象化openrouter-既定ローカルへ差替可)（クラウド強モデル前提へ回帰）・[ADR-058](#adr-058-llm-プロバイダ面別-providermodel-設定を-env-から-dbwebuisettings-へ移管する)（codex は一要素として撤去・DB+WebUI 化は現行）・ADR-059（codex 状態確認と reasoning フォールバックを撤去・embedding/openai reasoning は現行）・[ADR-018](#adr-018-無人運用の障害時方針失敗を黙って放置しない)（未設定面のフォールバックは不変）・[data-model.md](data-model.md)・[api.md](api.md)。

## ADR-074: 機関投資家のステルス仕込みを日足シグナル化する（stealth_accum・VWAP 分足は採らない）

- **状況**: 元機関トレーダーの解説動画（YouTube・出所 URL は知識カードに保持）を起点に、機関投資家の売買視点を AI Advisor の分析へ取り込みたいという要望。動画の核は 3 つ — ①**VWAP 反発**（機関は日中 VWAP より下で待つので VWAP 付近で反発しやすい）②**「株価が動かないのに出来高が増える」＝大口のステルス仕込み**（マーケットインパクトを避けレンジ内で売りを吸収）③**WhaleWisdom/13F で米大口フローを追う**。AssetVane は **夜間バッチ＋日足＋提示専用（自動売買なし・[ADR-009](#adr-009-日米業種リードラグ戦略は-assetvane-の分析機能とする自動トレードツールに持ち込まない)）** が芯。当初「VWAP を入れるか」という問いだったが、VWAP は本質的にザラ場（日中足）指標で、日足だと 1 日 1 本に縮退し、日中データ経路も日中 UI も無い提示型では発動タイミングに乗れない。J-Quants の分足は Light 以上の有料アドオン（月 +¥5,500・現行 light に上乗せ＝[ADR-008](#adr-008-j-quants-は-free-プランで開発運用時に有料へv2-を使う)）で、コストもアーキも噛み合わない。
- **決定**:
  - **VWAP（真のザラ場 VWAP）は採らない**。①VWAP 反発はデイトレのエントリー技で夜間バッチ提示型に乗らず、分足アドオン＋日中経路＋日中 UI と芋づるでスコープが跳ねる＝見送り。
  - **代わりに動画②「ステルス仕込み」を日足シグナル `stealth_accum` として実装する**（無コスト・分足不要・既存 `daily_quotes` のみ）。`quant/stealth_accumulation.py` の純関数（[ADR-016](#adr-016-手法はコードで実装する手法db-は索引でありコードの代替ではない)）＝**価格圧縮（直近 W=20 日 close の値幅/平均 < RANGE_MAX）× 出来高“持続”増（MA(vol,20)/MA(vol,60) ≥ VOL_ELEV_MIN・単日 spike の volume_spike とは別現象）× 時価総額フロア 500 億（仕手筋除外）× 長い下ひげ加点 × 低流動性除外**。score は連続値（0..1・保存フロア）。パラメータはコード同居の名前付き定数（[ADR-027](#adr-027-手法パラメータは-phase-1-はコード定数将来-method_settingsai-は助言自動改変しない)）。
  - **発火は「仕込み検出＋phase フラグ」の一本**。payload.phase＝`in_range`（レンジ内で仕込み継続）/`breakout`（仕込み後にレンジ上限を出来高伴いで上放れ）。下放れ（レンジ割れ）は None。動画の「見つける→ブレイクで乗る」二段を 1 signal で表現。
  - **配線**: `calc_signals` に append（時価総額は `repo.get_market_caps_by_code` で bulk 取得しループ前に 1 回・N クエリ回避）。`get_quotes` は既に OHLC を返すので `_quotes_df` を四本値まで widen（momentum/volume_spike は余分列を無視）。
  - **出力＝フルセット（発見＋深掘り＋解釈）だが専用 Tool は作らない**。①夜間 signals 保存 → notable 候補（[ADR-067](#adr-067-夜-digest-の注目シグナルを合流confluenceゲートai-選別に作り直す注目過多の解消)）に **⑤材料次元「stealth」**として合流。**phase=breakout（出来高確認あり）は単独でも候補（carve-out・出来高極増と同扱い）、in_range は 1 次元として合流に寄与**（広い母集団では単独で出さない＝digest 溢れ防止の ADR-067 精神を維持）。②深掘りは**既存 `get_signals(type='stealth_accum')` が payload を丸ごと返す**ので**専用 Tool は重複＝作らない**（[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け) の「事実は既存 Tool 戻り値で受け取る」・skill の「ツールを増やすと呼び忘れ経路が増える」）。get_signals の description に stealth_accum を明記して発見性だけ確保。③解釈は**専用の知識カードを作らない**＝momentum/volume_spike/lead_lag と同じく、シグナルの `payload.label`（「仕込み継続中（価格圧縮×出来高 X 倍）」「レンジ上放れ」）＋ `get_signals` の description で自己記述し、機関の仕込みフレーム（Wyckoff accumulation）は LLM の一般知識に委ねる。仕手除外は quant の時価総額フロアに焼き込み済みで AI に教える必要がない。手法ごとに level='general' の常時注入カードを増やすのは [ADR-062](#adr-062-知識カードを-corepolicy-に続く第-3-の知識源として-db-化する手法カードの再設計) が削減した always-inject 肥大の再来ゆえ避ける（既存シグナルにも手法解釈カードは無い＝consistency）。
  - **③WhaleWisdom/13F は本 ADR のスコープ外**（VWAP と無関係の別データ源・US 四半期・将来）。
- **理由**: 動画の実効的な核は VWAP でなく「価格が動かず出来高だけ増える」＝日足で完全に再現でき、既に `daily_quotes` の四本値・`volume_spike.py`（単日急増）・`market_cap`（valuation_snapshots）が揃っている。[ADR-016](#adr-016-手法はコードで実装する手法db-は索引でありコードの代替ではない)（手法はテスト済み純関数）・[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け)（Python が事実、AI は解釈）・[ADR-067](#adr-067-夜-digest-の注目シグナルを合流confluenceゲートai-選別に作り直す注目過多の解消)（合流ゲートで digest を溢れさせない）に素直に乗る。専用 Tool を足さないのは既存 get_signals で完結し冗長を避けるため。時価総額フロアは動画の「まともな機関は小型に入れない・小型の株価膠着＋出来高増は仕手筋」を実装したもの。
- **代替案**: **(A) 本物のザラ場 VWAP を入れる**（分足アドオン ¥5,500/月・日中経路・日中 UI）→ 夜間バッチ提示型と相性が悪くコストも跳ねる＝却下（US は Polygon 等で VWAP を安く引けるが JP が本丸で不整合）。**(B) 日足ローリング VWAP を indicators.py に足す**→ 日中反発の妙味は再現できず「粗い水準線」に留まる＝当面見送り（後から indicators に足すのは容易）。**(C) volume_spike を拡張して兼ねる**→ 単日 spike と「持続増×価格圧縮」は別現象で、閾値をいじると両方濁る＝独立シグナルに分離。**(D) 専用 screen Tool を新設**→ get_signals(type=…) と二重管理・呼び忘れ経路増＝却下。**(E) 手法解釈を知識カード（level='general'・常時注入）にする**→ 当初そうしたが、既存シグナル（momentum/volume_spike/lead_lag）は payload.label＋Tool description で自己記述しカード化していない・level='general' の常時注入は ADR-062 が削減した always-inject 肥大の再来＝却下し、カードは作らない（consistency）。
- **段階**: 実装済み（2026-07-02・ATDD＝受け入れテスト先行・**migration 不要**〔signals.signal_type は自由文字列〕）。`quant/stealth_accumulation.py`＋`test_quant_stealth_accumulation`（in_range/breakout 検出・圧縮/出来高/時価総額/低流動性/行数不足/下放れの各 None・下ひげ加点の 10 本）・`repo.get_market_caps_by_code`・`calc_signals` 配線・`services/notable` の⑤次元＋breakout carve-out＋`test_notable_builder` の 3 本追加・`get_signals` description 更新（`seed_knowledge_cards` は無改変＝他シグナル同様、手法解釈カードは作らない）。backend pytest／ruff／pyright green。**夜間バッチでの実生成（stealth_accum が signals に焼かれ notable/digest に出る）は次回運用時に確認**（`seed_knowledge_cards` は手動実行）。
- **関連**: [ADR-016](#adr-016-手法はコードで実装する手法db-は索引でありコードの代替ではない)（手法はテスト済み純関数）・[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け)（Python が事実・AI は解釈・既存 Tool 戻り値で受け取る）・[ADR-067](#adr-067-夜-digest-の注目シグナルを合流confluenceゲートai-選別に作り直す注目過多の解消)（notable 合流ゲート・digest 溢れ防止／⑤次元を追加）・[ADR-062](#adr-062-知識カードを-corepolicy-に続く第-3-の知識源として-db-化する手法カードの再設計)（知識カードで解釈を教える・linked_signal_type で手法↔計算索引）・[ADR-027](#adr-027-手法パラメータは-phase-1-はコード定数将来-method_settingsai-は助言自動改変しない)（手法パラメータはコード同居定数）・[ADR-009](#adr-009-日米業種リードラグ戦略は-assetvane-の分析機能とする自動トレードツールに持ち込まない)（提示専用）・[ADR-008](#adr-008-j-quants-は-free-プランで開発運用時に有料へv2-を使う)（分足はアドオン）。

## ADR-075: 手法カードをリポジトリ所有の第 4 知識源にする（signal_type キー・オンデマンド注入・linked_signal_type 非推奨化）

- **状況**: stealth_accum 追加（[ADR-074](#adr-074-機関投資家のステルス仕込みを日足シグナル化するstealth_accumvwap-分足は採らない)）で「手法の解釈をどこに置くか」が再燃した。裏取りの結果、**独自手法の解釈文脈が LLM に実質届いていない**と判明＝lead_lag（部分空間正則化 PCA・論文 SIG-FIN-036 忠実実装）は get_lead_lag の Tool description（入出力＋IC）止まりで機序・限界（取引コスト未検証・鮮度依存）が非到達／ai_alpha（当日内パーセンタイル順位の 60 日超過リターン予測）は専用 Tool すら無く label「AI 決算スコア」だけでスコア定義が不在／stealth_accum は ADR-074 で「payload.label で自己記述・カード無し」とした。GC/RSI/volume_spike は教科書で LLM の一般知識で足りるが、独自手法は説明が無いと誤解する。かつて共通の手法カード（`advisor/cards/*.md`・`method_cards.py` が起動時ロードし全プロンプトに**常時注入**）が存在したが、[ADR-062](#adr-062-知識カードを-corepolicy-に続く第-3-の知識源として-db-化する手法カードの再設計) で「全カード常時注入はスケールしない」を理由に廃止し knowledge_cards（DB・UI・AI triage・retrieval）へ寄せた。だが knowledge_cards は**アプリ/AI 所有**で、手法解釈のような**コード依存の正典**（追加には `quant/*.py` の実装が要る）を置くと「コード追加と同 PR でレビュー」が崩れる。`knowledge_cards.linked_signal_type`（[ADR-016](#adr-016-手法はコードで実装する手法db-は索引でありコードの代替ではない) の「手法↔計算の索引」）は設計されたが**注入には一度も使われず**（`WHERE linked_signal_type=…` はコードに無い）飾りのままだった。
- **決定**:
  - **手法カードを「リポジトリ所有・signal_type キー・オンデマンド注入」の第 4 知識源として新設**（CORE／POLICY／knowledge_cards に続く）。実体は `backend/app/advisor/method_cards/<signal_type>.md`（1 signal_type 1 ファイル・frontmatter に `signal_type`＋1 行 `summary`＋本文）。起動時に `method_cards.py` ローダが dict[signal_type] へ読む。**アプリ/AI からは追加・編集できない**＝手法追加は必ずコード変更（`quant/*.py`）を伴うので git・code review で入れる（[ADR-015](#adr-015-システムプロンプトは不変-core可変-policy-に分離する)「CORE は安定資産としてリポジトリ markdown」と同じ governance）。
  - **注入は skill 型 progressive disclosure（オンデマンド）**。①**メタ常時露出**＝`get_method_card` Tool の description に全カードの `signal_type`＋`summary` カタログを起動時に動的生成して載せる（Claude Code skill の name/description が常時見えるのと同型）。②**本文遅延ロード**＝`get_method_card(signal_type)` を呼んだ時だけフル本文を返す（引数省略なら一覧を返す）。**決定論的な自動注入はしない**（常時注入で破綻した ADR-062 の轍を踏まない＝必要な手法だけ LLM が能動ロード）。min_phase=1。
  - **knowledge_cards は無改変で市場文脈/外部/ユーザー知識に純化**（ガバナンス分離）。手法解釈は method_cards が所有。**`linked_signal_type` を非推奨化**＝手法↔signal の対応は method_cards がファイル名キーで持つため DB 列は冗長。`card_triage`（assist_card）が `linked_signal_type` を埋めるのを止め、schema コメントに deprecated を明記。**列 DROP は別の掃除 PR に回す**（本 PR は method_cards に集中・Minimal Impact／注入で未使用ゆえ残っても無害）。
  - **厚さ可変**＝教科書手法（momentum/volume_spike）は薄いカード、独自手法（lead_lag/ai_alpha/stealth_accum）は厚いカード。lead_lag は既存 `docs/methods/lead-lag.md`（開発者向け深掘り）から蒸留（数式は削り「何を測る／0..1 の読み方／限界」に）。開発者向け深掘りは docs/methods に残しカードから参照。
  - **ドリフト検査**＝起動時（またはテスト）に「登録 signal_type と method_cards ファイル」を突き合わせ、孤児カード/書き忘れを検出。
  - **[ADR-074](#adr-074-機関投資家のステルス仕込みを日足シグナル化するstealth_accumvwap-分足は採らない) の『stealth は無カード』を supersede**＝stealth_accum も method_cards を持つ。**ADR-062 の『手法解釈は knowledge_cards』をこの点だけ上書き**（市場/外部/ユーザー知識は引き続き knowledge_cards）。
- **理由**: 手法解釈は「いま見ているスコアの意味・限界」であり**コードの性質（`quant/*.py`）に従属**する＝コードと同じ寿命・同じレビューで管理すべき（リポジトリ所有）。app/AI 所有の knowledge_cards に置くと triage やユーザー編集で正典が揺れる。オンデマンドにするのは ADR-062 が潰した「全カード常時注入の肥大」を避けつつ、必要な手法だけ LLM が引けるから（skill 型＝メタ常時露出＋本文遅延ロード）。`get_method_card` を新設しても search_cards（knowledge 用）と役割が明確に別（手法の正典 vs 市場/外部知識の retrieval）で二重管理にならない。`linked_signal_type` 非推奨化は、対応の所有が method_cards に移り DB 列の存在理由が消えたため（実際に注入で未使用）。
- **代替案**: **(A) quant モジュールに `METHOD_CARD` 定数を埋め込む**→ 純関数モジュール（[ADR-016](#adr-016-手法はコードで実装する手法db-は索引でありコードの代替ではない)）に LLM 向け散文が混在・日本語長文の Python リテラル編集が痛い（E501）・手法は quant 1 ファイルに収まらない（lead_lag は quant＋services、ai_alpha は ml/）＝signal_type キーの markdown が筋。**(B) knowledge_cards に system/protected フラグを足して統合**→ 1 テーブルの整合性は上がるが app 編集不可の保護行と AI triage/retrieval が同居し governance が濁る・コード依存の正典を DB seed に置くと同 PR レビューが崩れる＝リポジトリ分離。**(C) 決定論注入（signal 発火→カード自動注入）**→ 常時注入寄りで ADR-062 の肥大を再来させ得る・オンデマンドが理想というユーザー方針＝却下。**(D) 既存 `docs/methods/*.md` をそのまま注入元にする**→ 開発者向けの数式・パラメータまで LLM に載る・docs/ の deploy 同梱保証が薄い＝backend/app 配下に蒸留版を持つ。**(E) linked_signal_type を今すぐ列 DROP**→ migration＋triage/router/UI/repo/test に波及し本 PR が太る＝非推奨化に留め別 PR。
- **段階**: 実装（2026-07-02・ATDD）。着手物＝① `advisor/method_cards.py`（frontmatter パーサ＋起動時ロード＋index/get＋ドリフト検査）② `advisor/method_cards/{momentum,volume_spike,stealth_accum,lead_lag,ai_alpha}.md` ③ Tool `get_method_card`（registry/schemas/handlers・description に動的カタログ）④ `card_triage` の linked_signal_type 出力停止＋schema コメント deprecated ⑤ テスト（loader/tool/ドリフト）⑥ skill `method-card-authoring` 新設 ⑦ docs 同期（本 ADR・advisor.md・CLAUDE.md・data-model.md〔linked_signal_type deprecated〕・ADR-074/062 に注記）。backend pytest／ruff／pyright green。**LLM が実際に get_method_card を呼んで解釈に使うかは次回運用時に確認**（migration 無し・列 DROP は別 PR）。
- **関連**: [ADR-062](#adr-062-知識カードを-corepolicy-に続く第-3-の知識源として-db-化する手法カードの再設計)（手法解釈を knowledge_cards に寄せた件をこの点だけ上書き・knowledge_cards は市場/外部/ユーザー知識に純化）・[ADR-074](#adr-074-機関投資家のステルス仕込みを日足シグナル化するstealth_accumvwap-分足は採らない)（stealth 無カードを supersede）・[ADR-016](#adr-016-手法はコードで実装する手法db-は索引でありコードの代替ではない)（手法はテスト済みコード・カードは参照でコードの代替でない）・[ADR-014](#adr-014-ai-は計算しないtool-calling-原則rag-は後付け)（事実は Tool 戻り値・AI は解釈）・[ADR-015](#adr-015-システムプロンプトは不変-core可変-policy-に分離する)（CORE＝リポジトリ安定資産と同 governance）・[ADR-067](#adr-067-夜-digest-の注目シグナルを合流confluenceゲートai-選別に作り直す注目過多の解消)（notable の材料次元と signal_type を共有）・[docs/methods/lead-lag.md](methods/lead-lag.md)（lead_lag カードの蒸留元）・[advisor.md](advisor.md)。
