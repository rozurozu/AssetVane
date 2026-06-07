# Dexter 調査メモ（AssetVane への参考機能の抽出）

> 調査日: 2026-06-07。対象は `~/Develop/dexter`（virattt/dexter・TypeScript/Bun・LangChain/Ink 製の金融リサーチ自律エージェント、CalVer `2026.6.3`）。
> **本書はスコープを「調査結果の記録」に限定する**（実装はしない）。AssetVane への採否・実装は別タスクで、本書を入力にする。
> 設計の真実は引き続き AssetVane 側の `docs/`（特に [decisions.md](decisions.md)）。本書の提案はすべて AssetVane の ADR 不変条件に従う前提で書く。
>
> **採用状況（2026-06-07）**: 候補のうち 3 つを ADR 化済み —— A（反追従＋ペルソナ）＝[ADR-041](decisions.md)、C（ChannelProfile）＝[ADR-042](decisions.md)、B（Advisor eval）＝[ADR-043](decisions.md)。実装は別タスク。

---

## 0. Dexter とは（要約）

「金融リサーチ特化の Claude Code」を標榜する**自律エージェント**。複雑な金融質問を計画に分解し、ツールで実データを取り、自己検証しながら答えに収束する。CLI（Ink）常駐＋WhatsApp ゲートウェイ＋cron/heartbeat の常時運用もできる。

| 観点 | Dexter | AssetVane | 根本差 |
|---|---|---|---|
| 性格 | **自律実行**（自分で深掘りループ） | **提示専用**（相談しながら提示・自動売買なし） | [ADR-001](decisions.md)/[ADR-009](decisions.md) |
| 計算 | **LLM がツール結果で計算もする**（DCF の算術は LLM が暗算） | **AI は計算しない**（quant 純関数が事実を計算） | [ADR-014](decisions.md)/[ADR-016](decisions.md) ← **最大の思想差** |
| 真実の置き場 | Markdown/JSON ファイル（`~/.dexter/`） | SQLite（DB に触るのは FastAPI だけ） | [ADR-005](decisions.md) |
| 基盤 | LangChain＋LangSmith（TS/Bun） | 自前 LLM アダプタ（Python/httpx・OpenRouter） | [ADR-012](decisions.md) |
| データ源 | Financial Datasets API（US株・SEC EDGAR）＋Exa/Tavily | J-Quants（日本株）＋自前 quant | [ADR-008](decisions.md)/[ADR-010](decisions.md) |

> **結論の先出し**: Dexter から採るべきは「**インフラ構造・運用の堅牢化パターン・プロンプト規律**」であって、「**LLM に計算/判断させる中身**」ではない。後者は AssetVane の ADR-014/016 と真っ向から衝突する。コードは TS/LangChain 依存で流用不可、**設計だけを Python に読み替える**。

調査は 6 領域を subagent で並行精読した: agent core / memory / skills・subagent / tools・finance / gateway・cron・heartbeat / model・evals・SOUL。

---

## 1. 採用候補ランキング（横断・優先度つき）

「効果 × AssetVane 規律との適合 × 移植コスト」で評価。**A=今すぐ価値が高い / B=中期 / C=任意・将来**。

| # | 採用候補 | 効く ADR/Phase | 区分 | ひとことで |
|---|---|---|---|---|
| 1 | **Advisor の eval（回帰検証スイート）** | [ADR-018](decisions.md)/[ADR-030](decisions.md)・Phase 3 | **A** | 「提案・Tool 呼び出しが正しいか」を継続検証する手段が AssetVane に無い盲点を直接埋める |
| 2 | **3層コンテキスト管理＋"数値を落とすな"要約規律** | [ADR-024](decisions.md)/[ADR-014](decisions.md)・Phase 3 | **A** | 常駐チャットのトークン肥大対策。要約で数字が化けると提案が壊れる→数値保全を要約規律に明文化 |
| 3 | **heartbeat の suppression（正常時は黙る）** | [ADR-018](decisions.md)・Phase 6 | **A** | 「失敗は黙らせない」(ADR-018) の裏返し「正常は鳴らさない」。digest の鳴りすぎ疲れを防ぐ |
| 4 | **Provider registry（単一真実源＋prefix 解決＋fastModel）** | [ADR-010](decisions.md)/[ADR-012](decisions.md)/[ADR-032](decisions.md) | **A** | 面別 provider 切替・将来 Ollama・コストを 1 テーブルに束ねる。dataclass で直訳可 |
| 4 | **fastModel に軽作業を逃がす** | [ADR-028](decisions.md) | **A** | ニュース/一般ニュース要約・compaction を安モデルへ。強モデル前提(ADR-012)を崩さずコスト削減 |
| 6 | **Prompt caching（system 先頭固定＋cache_control）** | [ADR-015](decisions.md)/[ADR-028](decisions.md)/[ADR-024](decisions.md) | **A** | 不変 CORE が毎回送られる構造に最適。input token を ~90% 圧縮し得る |
| 7 | **Temporal decay × evergreen 分離（retrieval）** | [ADR-013](decisions.md)/[ADR-014](decisions.md)（将来 RAG） | **B** | policy=減衰免除・journal=指数減衰。「方針は育てる/履歴は流れる」をスコアで実装 |
| 8 | **scratchpad のツール使用追跡（回数soft-limit＋類似クエリ警告）** | [ADR-024](decisions.md) | **B** | LLM のツール暴走・空振りを「ブロックせず警告注入」で抑える。提示専用に合う |
| 9 | **2層 tool 設計＋formatters（リーフ隠蔽＋markdown整形圧縮）** | [ADR-014](decisions.md)/[ADR-016](decisions.md) | **B** | 露出ツール数削減・トークン削減。**ルーターは「選択と引数生成だけ」に限定**すれば無矛盾 |
| 10 | **リッチ tool description の二重化（When NOT to Use・相互誘導）** | [ADR-010](decisions.md)/[ADR-012](decisions.md)・`advisor-pattern`(未作成) | **B** | 誤ツール選択を減らす。Free で 403 の tool を隠す手段にも |
| 11 | **cron/監視項目の動的 CRUD（自然言語→ジョブ）** | [ADR-011](decisions.md)・Phase 6 | **B** | チャットで「7203 が ±5% で通知」→ 監視登録。条件評価は quant・登録だけ LLM |
| 12 | **pre-compaction flush の金融特化要約プロンプト** | [ADR-029](decisions.md)/[ADR-014](decisions.md)・Phase 3 | **C** | 「耐久的事実だけ残し株価は記憶するな」→ journal 昇格の自動ドラフト生成 |
| 13 | **SOUL.md 流の投資哲学を CORE に明文化** | [ADR-015](decisions.md) | **C** | 規律だけでなく「なぜその規律か」を哲学として書きトーン・一貫性を安定 |
| 14 | **subagent の型別 allowlist＋read-only＋1段委譲** | [ADR-002](decisions.md)/[ADR-005](decisions.md)・Phase 4 | **C** | `investigate_stock` の並列深掘り雛形。書込除外が DB/承認規律と整合 |

---

## 2. ⚠️ 真似してはいけないもの（不変条件との衝突）

採否を誤ると ADR 違反になる箇所。**機構は採ってよいが「中身」は採らない**。

- **【最重要】DCF skill の「計算手順を自然言語で書いて LLM に算術させる」中身**（`src/skills/dcf/SKILL.md`）。Dexter は `get_financials` で生データを取り、CAGR・WACC・割引・感応度を **LLM が手順書に従って暗算**する。これは [ADR-014](decisions.md)（AI に数値を計算させない）/[ADR-016](decisions.md)（手法はテスト済みコードで・LLM にその場計算させない＝再現性・backtest が壊れる）と**真っ向から衝突**。
  - 採るなら: SKILL/カードは「**どの計算 Tool（Python quant）をどの順で呼ぶか**のオーケストレーション指示」に留め、算術ステップは書かない。DCF は `compute_dcf` のような**テスト済み計算 Tool 1 本**に置き換える。
- **ベクトル検索の全 chunk 総当たり**（`src/memory/search.ts` の `searchVector`）。個人スケールの割り切り。AssetVane も journal/dossier 数百〜数千なら許容だが、全銘柄ニュースまで広げるなら [ADR-014](decisions.md) 既定路線の `sqlite-vec` に寄せる。安易に総当たりを移植しない。
- **ファイル正本＋fs.watch 同期**（`src/memory/indexer.ts`）。Dexter は Markdown が正本だが AssetVane は DB が正本（[ADR-005](decisions.md)/[ADR-020](decisions.md)）。「ファイル正本＋SQLite インデックス」を「**DB 正本＋派生テーブル/sqlite-vec インデックス**」に読み替える。`fs.watch` は不要（書き込みは FastAPI 内、UPSERT 時に同期 reindex）。
- **JSON ストア（`jobs.json`/`sessions.json`）**。tmp→rename の atomic 書き込みの発想だけ拝借し、状態は DB テーブル化（[ADR-005](decisions.md)）。
- **heartbeat/cron で「LLM が条件を判断して通知」**。AssetVane は条件評価を quant 純関数に閉じ込めるのが鉄則（[ADR-014](decisions.md)/[ADR-016](decisions.md)）。「**quant が閾値判定 → 該当時のみ LLM が文面生成**」の順に組み替える。
- **WhatsApp 双方向・allowlist/pairing/group/routing 機構**。単一ユーザー・認証なし・家庭内 LAN（[ADR-001](decisions.md)）には過剰。チャネル抽象の発想だけ残し、ルーティング/グループ/アクセス制御は捨てる。
- **LangSmith など外部トレース SaaS への eval 依存**。[ADR-001](decisions.md) の「外部公開しない・個人 1 人」からは過剰。pytest＋自前 LLM-as-judge で十分（後述）。

---

## 3. 領域別の発見と AssetVane への適用

### 3.1 agent core（loop / scratchpad / context 管理）

**構造**: `src/agent/agent.ts` の ReAct 反復ループ。独立した planning/self-reflection/loop-detection モジュールは**無く**、それぞれ別機構に溶けている。

- **3層コンテキスト管理**: ① `microcompact`（LLM 不使用・読み取り専用ツールの古い結果を `[cleared]` 置換・毎ターン）→ ② `compact`（fast model で 9 セクション固定テンプレに構造化要約。**"Numerical Data" を絶対に落とすなと明示**）→ ③ `truncateMessages`（物理削除・最終手段）。compaction 直前に memory flush。
- **scratchpad**（`scratchpad.ts`）: クエリ単位の作業の単一真実。**ツール使用回数（soft limit 3）とクエリ Jaccard 類似度（0.7）を追跡し、繰り返しを警告（ブロックせず）**＝実質の loop detection。
- **最終回答に専用 LLM 呼び出しは無い**（ツール呼び出しが無くなったターンの本文が答え）。別モデルを使うのは compaction と flush のみ。
- typed events（`tool_start/end`・`compaction`・`memory_flush`・`done{tokenUsage,iterations}`…）を yield し UI/可観測性に流す。

**AssetVane への適用**:
- **3層コンテキスト管理は軸2常駐チャット（[ADR-024](decisions.md)）のトークン肥大に直接効く**。とくに「数値を落とすな」要約規律は [ADR-014](decisions.md)（数字は Python が出す・LLM は解釈のみ）と噛み合う。要約で数字が化けると提案が壊れるため、**要約規律に数値保全を明文化**する価値が高い。
- scratchpad のツール使用追跡は、LLM の冗長ツール呼び出し・空振りループを「警告注入」で抑える。提示専用 AssetVane に合う（ブロックしない）。
- typed event 駆動は無人 cron の進捗・障害を機械可読にし、**縮退検知（observations 空＝[ADR-018](decisions.md)）の判定→通知集約**の土台になる。Dexter の `done` が「最大反復到達」「エラー」も同型で返すのは縮退検知に直結。
- **注意**: 自律ループそのもの（深掘り反復・並行サブエージェント・書込 approval）は AssetVane では過剰。**採るのは context 管理と scratchpad 規律**であってループの自律性ではない。Python 移植では `stripOldThinking`（tool_call_id ペアリングを壊さず本文だけ消す）を OpenAI/Anthropic メッセージ形式で再実装する点が肝。トークン推定 `len/3.5` は日本語で係数要調整。

### 3.2 memory subsystem

**構造**: **「Markdown が正本・SQLite は使い捨てインデックス」**の二層。`chunks`（content＋embedding BLOB）＋`chunks_fts`（FTS5/BM25）＋`embedding_cache`（content_hash キー）＋`meta`（provider_fingerprint）。

- 書き込み 3 系統: ① 明示 Tool `memory_update`（`MEMORY.md`/daily）、② **pre-compaction flush**（溢れる前に「耐久的事実だけ要約」・**金融特化プロンプト**＝目標/リスク許容度/売買理由は残す・株価/相場データは入れるな・不要なら `NO_MEMORY_TO_FLUSH`）、③ セッション会話の自動インデックス。
- 想起は **5 段ハイブリッド**: ベクトル(0.7)＋BM25(0.3) 合算 → minScore → **temporal decay（指数・half-life 30日・`MEMORY.md`/日付なしは evergreen で減衰免除）** → MMR 再ランク（多様性）→ top-K。**埋め込みが無くても FTS5 単独で動く**フォールバック。
- セッション開始時に `MEMORY.md`＋当日/前日 daily を system prompt に常駐注入。

**AssetVane への適用**:
- **二層構造は [ADR-013](decisions.md)/[ADR-029](decisions.md) とほぼ同思想**。AssetVane は `policy`＋`advisor_journal` を DB に持つが「想起レイヤ（検索インデックス）」が無く、journal が貯まると夜AIが過去の自分を引けない。**journal/一般ニュース/dossier を chunk 化し横断 semantic 検索する retrieval 層**は、将来 RAG（[ADR-014](decisions.md)/[ADR-016](decisions.md)）の具体形そのもの。
- **temporal decay × evergreen が policy/journal に直結**: `policy`=evergreen（常に効く方針・減衰なし）、`advisor_journal`=日付つき（古いものは自然減衰）。「方針は育てる／履歴は流れる」を retrieval スコアで実装できる。**外部依存ゼロの純関数なので `quant/` 配下のテスト済み純関数として移植**するのが [ADR-016](decisions.md) に最適合。
- pre-compaction flush の金融特化プロンプトは **[ADR-029](decisions.md)（チャット重要点の journal 昇格）の自動ドラフト生成**にほぼ完成形で転用可。「株価は記憶するな」は [ADR-014](decisions.md) と同規律。
- `provider_fingerprint` による埋め込み次元の安全管理は、ローカル(Ollama)↔クラウド([ADR-012](decisions.md)) で embed モデルを差し替える AssetVane で事故防止に効く。
- **最小有効構成**: 5 段全部は要らない。**「FTS5（BM25）＋temporal decay」の 2 段で十分実用**。ベクトル/MMR は journal が増えて意味検索不足が実測で出てから足す（Phase を切る価値あり）。DB 正本へ読み替え、ベクトルは `sqlite-vec`（総当たりは移植しない）。

### 3.3 skills / subagent

**構造**:
- SKILL.md = YAML frontmatter（`name`/`description`）＋markdown 本文（手順）。起動時スキャンで discovery、**メタデータ（name/description）だけ常時 system prompt に露出・本文は `skill` ツールで選ばれた時に遅延ロード**（progressive disclosure）。本文の相対リンクを絶対パス化して返し、補助 md（`sector-wacc.md`）を `read_file` で引ける。
- 「1 クエリ 1 回まで」を**プロンプト規律＋コード強制（scratchpad で再呼び出しを黙ってドロップ）**の二重防御。
- subagent（`spawn_subagent`）= 隔離した別ループに委譲し**最終回答 1 本だけ返す**（中間出力で親を汚さない）。型別（general/research/analysis）に system prompt＋tool allowlist＋反復上限を束ね、**read-only 限定・1 段のみ（再帰委譲不可）**。

**AssetVane への適用**:
- **「メタデータ常時露出＋本文遅延ロード」は将来 `method_cards`＋embedding（[ADR-016](decisions.md)）と思想一致**。手法カタログ②（コードへの索引）を「軽量カードを LLM に見せ、選ばれたら詳細をロード」で実現する具体形。embedding 検索版は静的スキャンの一歩先。
- 補助 md 分割は AssetVane の参照知識③（repo markdown 手法カード）と完全同型で矛盾しない。
- subagent の型別 allowlist＋read-only＋1段委譲は、`investigate_stock`（[ADR-020](decisions.md)）の並列深掘り雛形。**書込除外が DB 単一書き手・冪等 UPSERT（[ADR-002](decisions.md)/[ADR-005](decisions.md)）と整合**。
- **衝突の核心（再掲）**: Dexter の DCF skill は計算ロジックを SKILL.md に書き LLM に算術させる＝[ADR-014](decisions.md)/[ADR-016](decisions.md) 違反。**整合するのは「メタ露出/遅延ロード/1回制約/補助md分割/discovery」というインフラ構造だけ。衝突するのは「本文に何を書くか」**。AssetVane では本文は「Python 計算 Tool のオーケストレーション指示」に限定する。
- 二度起動防止のコード強制（プロンプト＋scratchpad の二重化）は良い実践。

### 3.4 tools / finance

**構造**:
- registry（`src/tools/registry.ts`）が `{name, tool, description(リッチ), compactDescription, concurrencySafe}` を 1 箇所に束ね、**env キー有無で動的に有効化**（キーのある web search プロバイダだけ積む等）。`concurrencySafe` で読み取り系だけ並列。
- **2層 tool 設計（最重要）**: 下層＝薄いリーフツール（zod＋API＋整形）。上層＝**LLM ルーター・メタツール**（自然言語 `query` 1 つを受け、ネイティブ tool calling でリーフへルーティング、並列実行、`formatters` で **markdown テーブル化（5-10倍圧縮）**、`_errors` 集約）。registry に出すのは上層 4 つ＋web 系のみ（リーフは隠蔽）。
- search は優先プロバイダ→残りを順に try（全滅で初めて throw）のフォールバック連鎖。`web_fetch` は手動リダイレクト制御＋SSRF ガード＋HTML→markdown＋**fast model 要約**＋URL 単位 LRU(15分)。
- tool description は専用 dir でなく各ツールに `XXX_DESCRIPTION`（## When to Use / ## When NOT to Use / ## Usage Notes）を併設＝**schema 用の短い説明と、人間可読の運用ガイドの二重化**。キャッシュは「ディスク永続（破損自動削除・書込失敗握り潰し・TTL は読み出し時判定）」＋「LRU メモリ」の 2 系統。

**AssetVane への適用**:
- **2層 tool 設計は [ADR-014](decisions.md)/[ADR-016](decisions.md) と合致**。AssetVane は既に「quant が事実・handler が薄包み」。上に `get_financials` 風の自然言語ルーターを 1 枚被せると露出ツール数を減らしつつティッカー/日付/limit 解決を LLM に委ねられる。**ただしルーターは「選択と引数生成だけ」に限定**（数値は触らせない）すれば無矛盾。
- formatters レジストリ（ツール名→markdown 整形）は `services` 層に置けばトークン削減と可読性を両取り。**事実を渡す（[ADR-014](decisions.md)）を保ったまま表現だけ最適化**。
- `web_fetch` の取得パイプライン（リダイレクト制御・SSRF ガード・fast model 要約・URL 単位 LRU）は **`NewsAdapter`（[ADR-020](decisions.md)）の堅牢化に直接転用**でき、「本文を捨て要約と URL のみ保持」という既存方針と同思想。LRU は同一巡回内の重複取得を防ぐ。
- リッチ description の二重化（When NOT to Use・相互誘導）＋env/プラン有無での条件付き登録は、未作成の `advisor-pattern` スキルの素材になり、**Free で 403 になる tool を LLM から隠す手段**（[ADR-010](decisions.md)/[ADR-012](decisions.md)）にもなる。
- **注意**: finance リーフと router prompt（AAPL/GICS/SEC item-1A 等）は US 前提で全面書き換え。ルーター用追加 LLM 往復はコスト・レイテンシ要注意（fast model に回す＝[ADR-028](decisions.md)）。Playwright browser は `headless:false`・内部 API 依存で**無人 cron に載らない**（[ADR-020](decisions.md) の「夜は軽め」と同じ落とし穴）。DB は触らせず外部 API 並列だけ async（backend-foundations 規律）。

### 3.5 gateway / cron / heartbeat

**構造**:
- 単一プロセスで WhatsApp チャネル＋cron runner＋heartbeat を同居起動（AssetVane の「FastAPI 内に APScheduler 同居」と同型）。
- cron: スケジュール 3 種 union（`at` 一回／`every` 間隔／`cron` 式＋tz）×fulfillment 3 種（`keep` 継続／`once` 初回で自動 disable／`ask` 継続可否を尋ねる）。単一タイマーループ・毎 tick で `jobs.json` 再読込（ツール変更が即反映）・指数バックオフ・N 連続失敗で自動 disable・active hours 判定。
- **heartbeat = 自発的定期チェック**を独立機構にせず「**特別な名前の cron ジョブ**」として実装。`.dexter/HEARTBEAT.md`（チェックリスト）を market hours 中に N 分ごと巡回し、**特筆事項がある時だけ通知・なければ厳密に `HEARTBEAT_OK` だけ返せ**。
- **suppression**（最重要）: `HEARTBEAT_OK`／空応答／「no action needed」等の取りこぼし表現を正規表現で抑制＋**24h 以内の同一文面は重複抑制**＝「正常時は黙る」を多重防御。
- cron-tool/heartbeat-tool で**自然言語の依頼から監視を動的 CRUD**（「AAPL が $200 で教えて」→ fulfillment=once のジョブ）。

**AssetVane への適用**:
- **heartbeat = 夜AI の「常時監視版」への拡張（最有力）**。夜間 1 回だけの夜AI に対し、日中（Light 以上や指数なら遅延の影響小）に watchlist 急変・指数閾値超え・速報を定期チェックし**気づいた時だけ Discord**＝「Signal Beacon（Phase 6）の常時監視版」。batch-pattern（[ADR-011](decisions.md) 1 つの脳・2 つの起動口）に「**3 つ目の起動口＝heartbeat tick**」を足す形で自然に乗る。
- **suppression は [ADR-018](decisions.md) と相補的**: ADR-018=「失敗を黙らせない」、suppression=「正常を鳴らさない」。Phase 6 の digest に組み込めば鳴りすぎ疲れを防ぐ。**即効性が高い**。弱モデルが `HEARTBEAT_OK` を守らない前提の正規表現フォールバックは AssetVane でも保険として有効。
- cron 動的 CRUD は Phase 3 Advisor に `manage_watch` Tool（min_phase=6 想定）を足す具体像。チャットで「7203 が ±5% で通知」→ 監視テーブル登録。**[ADR-014](decisions.md) と無矛盾**: 閾値評価は quant、登録・解釈だけ LLM。`fulfillment=once`（目標到達で自動停止）は価格アラート spam 防止に有用。
- N 連続失敗で自動 disable・指数バックオフは AssetVane 未実装の堅牢化（取り込み価値あり。ただし無人運用では disable より「通知して継続」が安全な場合もあり要検討）。
- Discord 双方向化（外出先から Discord で Advisor と対話）は中期。現状は Webhook 片方向で、双方向は Bot 化＝[ADR-007](decisions.md) 見直しになる。**まず片方向強化（heartbeat・suppression・動的監視）が費用対効果が高い**。
- **注意（再掲）**: 「LLM が監視ループ内で条件判断」は採らない。「quant が閾値判定→該当時のみ LLM が文面生成」に組み替える。JSON ストアは DB テーブル化。ルーティング/グループ/アクセス制御は単一ユーザーには不要。

### 3.6 model / evals / SOUL.md

**構造**:
- **Provider registry**（`src/providers.ts`）が単一真実源。`{id, modelPrefix, apiKeyEnvVar, fastModel, contextWindow}` を 1 行ずつ（8 プロバイダ）。`resolveProvider` がモデル名 prefix（`claude-`/`gemini-`/`openrouter:`/`ollama:`…）で判定、未一致は OpenAI。**OpenAI 互換（xAI/OpenRouter/DeepSeek 等）は `baseURL` 差し替えだけで新規クラス不要**。
- 呼び出し層（`llm.ts`）: `callLlm`/`callLlmWithMessages`/`streamLlmWithMessages`。指数バックオフ retry＋非リトライ系（認証エラー）即 throw。usage は token 数のみ抽出（**USD コストは取らない＝この点は AssetVane の [ADR-028](decisions.md) の方が進んでいる**）。
- **Prompt caching**: Anthropic だけ system に `cache_control: ephemeral` を明示付与（input token ~90% 削減）。OpenAI/Gemini は自動キャッシュ。**system を配列先頭固定にしてヒット率を上げる**。
- **fastModel**: 各 provider に安モデル（haiku/flash/mini）。用途は compaction と web_fetch 要約。
- **evals**（`src/evals/`・LangSmith）: CSV データセット（236 問・question/answer/type/rubric）に対し**フルエージェントを実走**させ、最終回答を **LLM-as-judge（structured output で `{score:0|1, comment}`）**で採点。**正答率のみ・Tool 呼び出し自体は採点していない**。
- **SOUL.md → CORE**: ペルソナ＋投資哲学（Buffett/Munger）を散文で書き、`buildSystemPrompt` が `## Identity` として注入＋"embody this" で締める。ユーザー上書き（`.dexter/SOUL.md`）→ バンドルのフォールバック。RULES.md（可変ルール）＋Memory も多層注入。`.dexter/settings.json` に provider/model 永続化・`/model` コマンドで切替＋API キー誘導。

> **ペルソナ／ハーネスの深掘りは別冊 [dexter-harness.md](dexter-harness.md) を参照**（システムプロンプト全層の分解・SOUL.md の構造・チャネルプロファイル・反追従の多重強調・AssetVane の CORE 多層化への適用＝[ADR-015](decisions.md)/[advisor.md §2](advisor.md)）。

**AssetVane への適用**:
- **eval が本調査の核心（最重要）**。AssetVane の盲点は「[ADR-018](decisions.md) の縮退検知はランタイム防御に留まり、"そもそも Advisor が正しい提案・正しい Tool 呼び出しをしているか" の回帰検証手段が無い」こと。Dexter 流のゴールデンセット（市況・ポートフォリオ→期待される Tool 呼び出し・観点）を作れば、ADR-018 が拾えない「縮退ではないが提案がズレ」「Tool を呼ぶべき場面で生データ解釈に走った（[ADR-014](decisions.md) 違反）」を回帰検出できる。**Dexter は最終回答のみ採点だが、AssetVane は一歩進めて「Tool 呼び出し・observations 非空・数値捏造監査」を採点軸に加える**と [ADR-018](decisions.md)/[ADR-030](decisions.md) の盲点を直接埋められる。**実装は LangSmith を捨てて pytest＋自前 LLM-as-judge**（[ADR-016](decisions.md) のテスト規律と整合・追加依存なし）。ただし実 LLM を叩くので **CI 常時実行ではなく手動/nightly の別レーン**にする（testing-strategy の「ネットに出ない」原則と衝突するため分離）。
- Provider registry は **面別 provider 切替（[ADR-032](decisions.md)）・将来 Ollama（[ADR-012](decisions.md)）・コスト（[ADR-028](decisions.md)）を 1 テーブルで束ねる**。Python の dataclass＋dict registry に直訳可。`fastModel` で軽作業を安モデルに逃がすのが [ADR-028](decisions.md) に即効（`NewsAdapter`/`general_news` 要約・compaction）。
- **Prompt caching は不変 CORE（[ADR-015](decisions.md)）が長く毎回送られる構造に最適**。常駐チャット（[ADR-024](decisions.md)）・夜間バッチの input token を圧縮し [ADR-028](decisions.md) コストを直接下げる。ただし OpenRouter 経由はキャッシュ挙動がモデル/プロバイダ依存で一様でないため、**OpenAI/Anthropic を直叩きする面だけ明示キャッシュ、OpenRouter 経由は自動任せ**と面別に割り切る。
- SOUL.md 流に CORE へ「規律だけでなく哲学（margin of safety・invert・circle of competence・"I don't know" の知的誠実さ・accuracy over comfort）」を明文化すると提案の一貫性・トーンが安定（[ADR-015](decisions.md)）。"accuracy over comfort"＝方針に反するデータを隠さず突きつける、は AssetVane の「提示に徹する」と直結。
- **注意**: コードは LangChain/LangSmith 依存で流用不可、**設計（registry/prefix/fastModel/caching 分岐）だけ移植**。USD コスト計上は AssetVane が既に勝っており Dexter から学ぶものはない。

---

## 4. 次アクションの叩き台（実装は別タスク）

優先度 A から段階的に。各々は別途 grill/ADR 化してから着手する想定。

1. **【A・Phase 3】Advisor eval スイート（pytest＋自前 LLM-as-judge）**: 固定シナリオ（市況・ポートフォリオ）の DB を一時 SQLite で組み、Advisor を実走→ judge で「期待 Tool が呼ばれたか・observations 非空か・数値捏造がないか（[ADR-014](decisions.md) 監査）・提案の妥当性」を採点。nightly/手動の別レーン。→ [ADR-018](decisions.md)/[ADR-030](decisions.md) の盲点を埋める。
2. **【A・Phase 3】コンテキスト管理＋数値保全要約**: 軸2チャットに microcompact 相当＋「数値を落とすな」要約規律。[ADR-024](decisions.md)/[ADR-014](decisions.md)。
3. **【A・Phase 6】heartbeat（日中の自発巡回）＋suppression**: batch-pattern の 3 つ目の起動口。閾値判定は quant、文面のみ LLM、正常は黙る。[ADR-018](decisions.md)。
4. **【A・横断】Provider registry ＋ fastModel ＋ prompt caching**: 面別切替（[ADR-032](decisions.md)）の整理ついでに registry 化し、要約系を fastModel・CORE を caching でコスト削減（[ADR-028](decisions.md)）。
5. **【B】retrieval 層（FTS5＋temporal decay の 2 段）**: journal/一般ニュースの横断検索。evergreen=policy・減衰=journal。将来 RAG（[ADR-014](decisions.md)）の最小実装。
6. **【B】2層 tool＋formatters・description 二重化・cron 動的 CRUD**: 露出ツール削減・誤選択低減・チャットからの監視登録。

> どれも「機構は Dexter・中身は AssetVane の ADR 準拠」が原則。とくに **eval（#1）と heartbeat/suppression（#3）が、現状 AssetVane に無い能力を最小コストで足せる**ため費用対効果が高い。

---

## 5. 参照ファイル（Dexter 側・調査の出所）

- agent core: `src/agent/{agent,scratchpad,compact,microcompact,types,prompts,tool-executor}.ts`、`src/memory/flush.ts`
- memory: `src/memory/{database,search,temporal-decay,mmr,flush,indexer,chunker,session-files}.ts`、`src/tools/memory/{memory-search,memory-get,memory-update}.ts`
- skills/subagent: `src/skills/{types,loader,registry}.ts`、`src/skills/dcf/{SKILL.md,sector-wacc.md}`、`src/tools/skill.ts`、`src/tools/subagent/{spawn-subagent,types,progress}.ts`
- tools/finance: `src/tools/registry.ts`、`src/tools/finance/{get-financials,get-market-data,read-filings,screen-stocks,formatters,api,utils}.ts`、`src/tools/search/web-search.ts`、`src/tools/fetch/{web-fetch,utils}.ts`、`src/tools/browser/browser.ts`、`src/utils/cache.ts`
- gateway/cron/heartbeat: `src/gateway/gateway.ts`、`src/cron/{runner,executor,schedule,store,types,heartbeat-migration}.ts`、`src/gateway/heartbeat/{prompt,suppression}.ts`、`src/tools/cron/cron-tool.ts`、`src/tools/heartbeat/heartbeat-tool.ts`、`src/gateway/{agent-runner,sessions/store,channels/{types,manager}}.ts`
- model/evals/SOUL: `src/providers.ts`、`src/model/llm.ts`、`src/utils/{model,config}.ts`、`src/controllers/model-selection.ts`、`src/agent/prompts.ts`、`SOUL.md`、`src/evals/run.ts`、`src/evals/dataset/finance_agent.csv`
