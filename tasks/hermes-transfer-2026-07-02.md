# hermes-agent → AssetVane 移植提案（自己改善ループ）

- 作成: 2026-07-02
- 出所: `/Users/okada/Develop/hermes-agent`（Nous Research の自己改善 AI エージェント）を実コードまで読み込み、AssetVane の ADR 制約に合わせて翻訳した多エージェント分析（26 エージェント・実装ファイル裏取り済み）。
- 位置づけ: **設計探索メモ（正本ではない）**。着手する束を選んだら、その時点で ATDD＋新 ADR に落として `docs/decisions.md`／`docs/roadmap.md` を同期する（[[assetvane-workflow-atdd-adr]] の作法）。

> **⏱ 進捗（2026-07-03）**: **テーマ A（★1 採点ループ）＝実装済み・自動＋実機 E2E 検証済み（ADR-077）**／**D-1 FTS5 recall（★2）＝実装済み・自動＋実機 E2E 検証済み（ADR-078）**／**テーマ B（★3 経験蒸留 reviewer 面）＝実装済み（2026-07-03・ADR-081・pytest 1131 green）**＝ループを閉じる本丸。採点済み outcome を教材に reviewer 面の Tool ループが知識カード draft を蒸留（活性化は人間・ADR-009）。活動量ゲート（新規 final <閾値で skip）＋過学習足切り（count≥min_samples の傾向だけ）＋toolset 制限（多重防御）＋source 強制・migration 不要。AI が実際に Tool を呼ぶ E2E は ★1・★2 と同じく dev の LLM 未設定で未（次回運用時）。**テーマ C（★4 投資家プロファイル記憶層）＝実装済み（2026-07-03・ADR-082・`/grill-me` で設計合意→ATDD）**＝ループの「誰に返すか」を閉じる層。policy と分離した「行動の癖の記述」を独立の単一行 `investor_profile`〔0039〕に持ち、夜バッチ profiler 面〔7 面目・reviewer 同型〕が取引台帳の行動信号（手仕舞いの帰結・ディスポジション・関心集中＝`quant/behavior.py`）から傾向メモを承認制（proposals kind=`profile_note`）で蒸留→人間が /profile で承認→本文に追記。使い方は鏡・反追従（迎合しない）。注入は軸1/軸2 の第 3 層。`propose_profile_note` は allowlist_only で profiler 限定。migration は 0039 のみ。pytest/ruff/pyright/biome/tsc green。AI 実呼出し E2E は他機能と同じく dev LLM 未設定で未。テーマ D/E（★5 学習グラフ〔A 依存〕・★6 cron）は**未着手**。詳細は末尾の「§7 実装ステータス」「§8」を参照。

---

## 1. hermes-agent とは（自己改善ループの要点）

hermes の中核は「エージェントが自分の経験を後から資産化する**閉じた学習ループ**」。骨格は次の 8 段。

1. **経験の蓄積**: 会話・ツール実行の軌跡を残す。
2. **背景レビュー（background review fork）**: 本流とは別コンテキスト／別権限のフォークが、ターン後に軌跡を読み直す（`agent/turn_finalizer.py`・`agent/background_review.py`。ツール反復 `_iters_since_skill` が閾値超で発火＝「複雑タスクほど学ぶ」）。
3. **知識の蒸留（skill-creation）**: 「繰り返し効いた/外した手順」だけを再利用可能なスキル/記憶へ書き起こす。乱造せず「まず既存を検索→改訂優先」。
4. **誤学習防止**: 一過性エラー・単発の失敗・環境依存の不運は durable 化しない。
5. **想起（session-search / context-engine）**: 次回、過去の類似局面を FTS5（`messages_fts` トリガ自動同期・trigram で CJK 部分一致・LLM 不使用）や意味検索・会話フォールドで引き当てる。
6. **自己像のモデリング（user-modeling）**: Honcho の dialectic reasoning で「相手が誰か」を継続 derive し system prompt に注入。
7. **可視化と剪定（learning-graph）**: `agent/learning_graph.py::build_learning_graph()` が学びをノード/エッジ化し人間が枝刈り（`/journey`）。
8. **nudge / [SILENT] / cron**: 定期的に「学ぶことはあるか」「報告に値するか」を問い、変化時のみ動く。

**AssetVane に写すときの決定的な差し替え**は、教師信号を「会話の読み直し」から「**市場結果による事後採点**」に置くこと。投資では最強の教師は会話ではなく、過去提案 × その後の株価。これが ADR-014（AI に数値を計算させない）と完璧に噛み合う——実現リターンは Python 純関数が「事実」として計算し、LLM は解釈と草案化だけを担う。

### hermes 概念の抽出結果（11 件）

| 概念 | 一行 | 自己改善 |
|---|---|---|
| 閉じた学習ループ | skills・memory・nudges・session search・user modeling の 5 部品連携メタ設計 | ○ |
| skill-creation | 複雑タスク後にフォークが SKILL.md を自律新規作成/追記 | ○ |
| skill-self-improvement | ターン後に分身が会話を再生し SKILL.md を最小差分 patch | ○ |
| memory-and-nudges | MEMORY.md/USER.md を自己編集＋Nターンごとの自己催促 | ○ |
| context-engine | 文脈長閾値で head/tail 保護＋中間 LLM 要約する差替層 | × |
| session-search-fts5 | FTS5 全文検索＋bookend で「目的→ヒット→結論」再構成（LLM 不使用） | × |
| user-modeling | Honcho dialectic でユーザ像を derive し毎ターン注入 | × |
| learning-graph-journey | 学んだスキル/記憶をグラフ化し可視化・編集 | ○ |
| cron-automations | 自然言語＋人間可読スケジュールで無人定期実行・任意配信 | × |
| subagents-rpc | 独立サブエージェント並列委譲＋LLM が書いた Python が Tool を RPC | × |
| trajectory-datagen | 軌跡をバッチ生成＋頭尾保護圧縮し学習データ化 | ○ |

---

## 2. AssetVane の現状資産（既にあるもの／欠けているもの）

### 既にあるもの（ループの部品はほぼ揃っている）

| 領域 | 既存資産 |
|---|---|
| 知識の器 | `knowledge_cards`（ADR-062・weight/status/level/market/code/embedding・意味検索 `search_knowledge_cards`・`/cards` 管理）＝hermes の「スキル」に最も近い |
| 承認制起票 | `propose_card`/`adjust_card_weight`/`submit_journal`/`propose_trade`/`submit_notable_stocks`（検証 only）→ `persist_*_from_tool_runs`（W2）→ 人間 active 化（ADR-009） |
| エピソード記録 | `advisor_journal`（observations/proposal/policy_snapshot/situation_briefing）・`notable_picks`（ADR-067・0032） |
| 帰結列の"器" | `proposals.outcome`/`resolved_at`/`journal_id`/`status`（data-model.md が「提案精度の検証に使う」と明記） |
| 夜バッチ基盤 | `NIGHTLY_JOBS`（ADR-011「1 つの脳・2 つの起動口」）・lock・stop_aware・`JobResult` 集約・`notify_digest`＋`send_once` 冪等 |
| 意味検索/埋め込み | ADR-045 の `vec_distance_cosine`・`embed_cards`/`embed_news`・`embedding_config`（DB+WebUI） |
| 事実計算の分業 | `quant/*.py` 純関数（`notable.py`/`lead_lag.py`/`valuation.py`）＋ `get_recent_adj_closes_by_codes` |
| 独立 LLM 面 | FACES（chat/nightly/dossier/tagger/triage）・面別割当（ADR-058/059） |

### 欠けているもの（3 セクションが揃って最重要と名指し）

- **outcome フィードバックが実質ゼロ**: `proposals.outcome` は列だけで**読む箇所が backend 全体でゼロ**。`notable_picks` は write-only。`NIGHTLY_JOBS` は全て forward-looking（取得→計算→タグ→助言→通知）で「昨日を振り返る」レトロジョブが 1 本も無い。
- **過去判断が次ターンに戻る帯域が n=1**: 夜AI・チャットとも `get_recent_journal_summary(n=1)` の直近 1 件だけ。却下理由・過去の帰結が集約されず、同じ提案を繰り返せる。`advisor_journal` は embedding を持たず**意味検索対象外**（時系列窓のみ）。
- **却下シグナルが CORE/POLICY/カードへ還元されない**・**カードの有効性が実績で淘汰されない**（weight は主観手動）・**journal→カードの蒸留経路が無い**。
- **ユーザー訂正が記憶されない（lessons.md 相当が不在）**・**反追従ペルソナ（ADR-041）が未実装**。
- **提案品質の eval ハーネスが無い**（モデルの CV IC/lead_lag hit_rate はあるが、Advisor の提案レベルの成績集計が無い）。

**結論**: 部品はほぼ全部ある。欠けているのは「市場が採点する backward-looking ジョブ」と「採点を次ターンへ戻す経路」の**接続**だけ。ゼロ新設ではなく配線でループが閉じる。

> ⚠️ 上記の「未使用列」「n=1」「backward-looking ジョブ皆無」はエージェントが実コードを grep して裏取りした所見だが、着手前に該当箇所を再確認すること。

---

## 3. 移植提案（テーマ別・5 束）

### テーマ A：市場結果で採点する閉ループ（**骨格・最優先**）

- **何をする**: `NIGHTLY_JOBS` に夜バッチ初の backward-looking ジョブ `score_past_proposals` を追加。過去 `proposals`/`notable_picks` を日付・銘柄で読み戻し、提案日終値→N営業日後の実現（超過）リターンと的中フラグを quant 純関数で計算し、専用台帳 `proposal_outcomes` に冪等 UPSERT。成績を次回プロンプトへ注入。
- **既存のどこを拡張**: ①`proposals.outcome`/`resolved_at`（既存・未使用）＋新台帳 `proposal_outcomes(source,code,proposed_at,horizon,entry_close,realized_return,hit,sample_meta)` に `notable_picks` を合流。②新 `quant/outcome.py::realized_return()`（`get_recent_adj_closes_by_codes` の終値列から計算・DB 非依存・pytest 付き＝ADR-016）。③`get_track_record` Tool（min_phase=1・事実取得系・tool_runs 非搭載）または `prompt_builder.build_messages` の `get_recent_journal_summary` 隣に「直近の自分の成績」ブロック。
- **投資特有の価値**: 「承認/却下した buy/sell がその後上がったかを一切突き合わせていない」を初めて埋める。ソース別（夜AI vs チャット）・手法別（momentum/GC/RSI反転/lead_lag/ai_alpha 起点）・カード別 hit-rate を実測。
- **ADR整合**: ADR-014 と真っ向整合。needs-new-adr（予測 vs 実現の専用台帳・夜バッチ初の backward-looking・outcome→prompt 還流の 3 点が新パターン）。
- **規模/価値**: L / high。**リスク**: Free 12週遅延で採点が数週遅れる（「採点不能」を明示）／少サンプル過学習／提示ベース（提案日終値起点）と `transactions` の実 P/L を混同しない。

### テーマ B：経験蒸留（採点結果を知識カードへ）

- **何をする**: 活動量ゲートで発火する**振り返り専用 LLM 面（reviewer）**を FACES に triage 同型で 1 面追加。当日 journal＋proposals＋**採点済み outcome** を教材に、繰り返し効いた/外した知見だけを `propose_card` で draft 起票（承認制）。軌跡の頭尾（相談＋situation_briefing／最終提案）を無損失保護し中間 Tool 探索だけ定性要約に潰す（数値は verbatim）。
- **既存のどこを拡張**: FACES に reviewer 面（`/settings` で provider 割当・未設定は沈黙 skip＝ADR-018）。`persist_card_ops_from_tool_runs`（W2）を再利用。乱造防止＝`search_knowledge_cards` 近傍検索→近ければ `adjust_card_weight`/本文改訂 draft 優先（themes の `near_duplicate_of` 発想を cards へ）。
- **投資特有の価値**: journal→カードの蒸留経路の不在を埋める。「この catalyst 型 buy はガイダンス確認前だと過去 3 回中 2 回 miss」といった非自明知見だけカード化。
- **ADR整合**: 一部 aligns／一部 needs-new-adr（発火ゲート・reviewer 面の権限制限・過学習禁止規律の正本化）。draft 止め厳守で ADR-009/062 維持。ADR-029（会話揮発）と整合し生ログは永続しない。
- **規模/価値**: L / high（**A に依存**）。**リスク**: 過学習が最大（反復回数を Python 頻度カウントで足切り・単発トレード禁止）／承認疲労／毎晩の LLM コスト（活動量ゲート＋月次コストガード）。

### テーマ C：投資家プロファイル記憶層（ADR-041 の土台）

- **何をする**: `knowledge_cards.level` に `user`（=investor）を追加し、行動傾向・認知バイアス・繰り返す関心・過去の訂正を夜間 LLM で継続蒸留。CORE→POLICY に続く静的層「誰か」として注入（動的層＝既存 journal）。ユーザー訂正の記憶固定（lessons.md 相当の不在）を埋める。
- **既存のどこを拡張**: 新テーブル不要（`knowledge_cards` 再利用・level enum 追加の追補 migration のみ）。新ジョブ `distill_investor_profile` を `run_advisor` の後に。行動事実は `transactions`/`proposals` 突合を quant/services 純関数で計算。
- **投資特有の価値**: 台帳で裏取りできる記述的プロファイルが、未実装の ADR-041（反追従・行動コーチング）の起点。「急落局面で売った銘柄が後に戻る傾向」を市場結果で本人に返す。
- **ADR整合**: needs-new-adr。**記述（誰か）と規範（policy）を厳格分離**して ADR-013 の緊張を回避＝プロファイルは第二の policy にしない・版管理化しない・policy 変更は承認制 `policy_change` のみ。
- **規模/価値**: M / high。**リスク**: ステレオタイプ化・自己成就（weight 減衰＋再確認＋「傾向は仮説」）／嗜好の捏造（該当発話 grounding＋triage で rejected）。

### テーマ D：想起・記憶帯域の拡張／可視化

- **P6 判断ログ横断想起（FTS5）**: `advisor_journal`＋`proposals` に trigram FTS5 索引をトリガ自動同期し、埋め込み・LLM 不要のキーワード横断想起 Tool（read-only・min_phase=1）。bookend＝`proposals.rationale`（目的）→ observations（ヒット）→ `outcome`/status（結論）を depends_on 連鎖で束ねる。**帯域 n=1 問題を最安コストで直接改善**。needs-new-adr（**生チャットは非索引**の線引きで ADR-029 の揮発意図を守る／ADR-045 段階C ハイブリッドの第一歩）。M / high。リスク＝母集団が薄い（journal 1 日 1 件）ため索引が育つまで空振り。
- **P5 会話フォールド層（ChatContextEngine）**: `build_messages` 手前の非破壊 LLM 送信用射影層。head（CORE/POLICY/カード）不可侵・tail 保護・中間を**数値欄禁止の固定スキーマ**（thesis/catalyst/invalidation/確信度のみ・数値は Tool 再取得）で要約。needs-new-adr。M / **medium**。リスク＝over-engineering（強モデル大 context 前提で閾値到達が稀）。純圧縮でなく「壁打ちチェックポイント＋昇格候補提示（ADR-029 補填）」に主眼を置かないと採用理由が立たない。
- **P8 アドバイザー学習グラフ**: `knowledge_cards`＋journal チャンク＋policy_snapshot を安定 ID のノード/エッジグラフ化（`build_learning_graph()` 単一真実源・`GET /learning-graph`）。各ノードに Python 計算の帰結事実を添える。needs-new-adr。L / high（**A に依存**）。read-only の軌跡再生であって versioning ではない、と線引き。

### テーマ E：実行器・オーケストレーション

- **P9 スケジュール助言**: 「自然言語指示＋人間可読スケジュール＋配信先」を 1 レコード（`scheduled_watches`）に宣言し、APScheduler tick が `run_turn` で無人実行。hermes の `[SILENT]` を `notify_digest` の「変化時のみ配信」に写す。**採点ウォッチ（テーマ A）をコード改修でなくユーザー宣言で足せる器**。needs-new-adr（ADR-011 との緊張＝固定 `NIGHTLY_JOBS` の外側に動的ジョブ。折り合い＝別ロック・夜間 pipeline 後 or 独立 tick で隔離・quant 手法ではなく Advisor 呼び出しに限定）。M / high。hermes の chronos provider 抽象・JWT webhook・scale-to-zero・self-healing は単一ユーザー/LAN で過剰、意図的に落とす。
- **P10 分析デリゲート**: 候補ごとに読み取り専用サブエージェントへ並列委譲し要約だけ親に返す。**tension**＝hermes の code_execution 側（LLM が Python を書いて RPC）は ADR-016/014 に真っ向反するので**移植しない**（多段は Python の合成 Tool で畳む）。委譲自体も `investigate_stock` ドシエと役割重複。L / medium。優先度低。

---

## 4. 自己改善ループの具体設計（AssetVane 版 learning loop）

**設計の核**: hermes の「会話を読み直して学ぶ」を、**「市場結果で自分の過去提案を採点して学ぶ」**に差し替える。教師信号＝会話 → 市場結果。

```
① 判断を残す ─② 市場が採点 ─③ 成績を戻す ─④ 知識へ蒸留 ─⑤ 人間が承認 ─⑥ 可視化
   (act)         (grade)        (load)        (distill)      (approve)     (observe)
     └───────────────────────── 次ターンの act へ ──────────────────────────┘
```

### ① 判断を残す（既存＋薄い配線）
- 夜AI/チャットが `propose_trade`/`submit_notable_stocks`/`submit_journal` → `journaling.py` 共通経路 → `persist_*_from_tool_runs`（W2・`begin()` で atomic）→ **`proposals` / `notable_picks` / `advisor_journal`** に永続。
- 追加配線（P8 用・任意）: 起票時に注入されていた `knowledge_cards.id` 集合を proposal に焼く（因果エッジ用）。

### ② 市場が採点（**新規・骨格＝テーマ A**）
- **job**: `NIGHTLY_JOBS` 末尾に backward-looking な **`score_past_proposals`**（lock/stop_aware/JobResult 集約＝ADR-011/018・冪等 UPSERT・部分失敗再開可）。
- **quant**: **`quant/outcome.py::realized_return(entry_close, later_close[, index_close])`** が提案日終値→N営業日後の実現（超過）リターンと `hit` を**事実として計算**（DB 非依存・pytest 付き＝ADR-014/016）。終値供給は既存 **`repo.get_recent_adj_closes_by_codes`**。
- **table**: 専用台帳 **`proposal_outcomes`** に `notable_picks` も合流。既存 write-only な `proposals.outcome` に初めて読み手が付く。
- **規律**: N日未経過は「採点不能」で skip（Free 12週遅延を明示）。提案日終値起点の**提示ベース**で測り、`transactions` の実 P/L とは別。

### ③ 成績を戻す（新規）
- **tool**: **`get_track_record`**（min_phase=1・事実取得系・結果値は tool_runs に載せない＝ADR-025）。または `prompt_builder.build_messages` の `get_recent_journal_summary(n=1)` 隣に「直近の自分の成績」ブロック。
- **集計軸**: ソース別（夜AI/チャット）・手法別・注入カード別の hit-rate と平均実現リターン。n=1 帯域を初めて広げる。

### ④ 知識へ蒸留（新規＝テーマ B）
- **face**: FACES に **reviewer 面**（triage 同型・未設定は沈黙 skip）。**活動量ゲート**（当日 journal observations 数＋起票 proposals 数＋resolved outcome 数が閾値未満なら skip＝hermes の `_iters_since_skill` 相当）で「複雑な晩だけ」発火。
- **job**: `notify_digest` 直前に **`distill_experience`**。`run_turn` にカード管理 Tool だけを渡す（trade 系はブロック）。
- **乱造・過学習防止**: `search_knowledge_cards` 近傍→近ければ `adjust_card_weight`/本文改訂 draft。最小サンプル数は Python 頻度カウントで足切り（LLM 判断に委ねない）。単発の不運で weight を下げない＝hermes「一過性エラーを捕らえない」の投資版。

### ⑤ 人間が承認（既存・不変条件）
- 蒸留は必ず **draft**（`source=retrospective`）止まり。`/cards` で人間ワンクリック active（ADR-009/062）。weight 変更は `proposals(kind=card_weight)` → `resolve_proposal` が反映。
- **policy は自動改変しない**（ADR-013）。成績が「もっとリスクを取りたい」を示唆しても承認制 `policy_change` を促すだけ。

### ⑥ 可視化（新規＝P8）
- **`GET /learning-graph`**（`build_learning_graph()` 単一真実源）＋ `/proposals` or Dashboard に hit-rate/実現リターンウィジェット。

### hermes ↔ AssetVane 対応表

| hermes | AssetVane 版 |
|---|---|
| 会話を読み直して学ぶ | **過去提案を市場結果で採点して学ぶ**（教師信号を差し替え） |
| background review fork | 夜間 **reviewer FACE**（同一 FastAPI プロセス・別 spawn しない＝ADR-005） |
| skill 生成 → 承認 | `propose_card` draft → 人間 active（ADR-009/062） |
| 誤学習防止（一過性を捕らえない） | 最小サンプル足切り・単発の不運で weight を下げない統計規律 |
| 「まず既存を検索→改訂優先」 | `search_knowledge_cards` 近傍 → `adjust_card_weight`/改訂 draft |
| [SILENT] sentinel / nudge | `notify_digest` の変化時のみ配信・reviewer 活動量ゲート |
| session-search 想起 | P6 FTS5 recall（`advisor_journal`＋`proposals`・bookend=rationale→outcome） |
| learning-graph | P8 `GET /learning-graph`（read-only 軌跡再生・versioning ではない） |

**この閉ループの一線**（全 ADR 整合の要）: 自己改善＝**自動チューニングではなく、市場結果を Python が事実計算し人間に可視化して意思決定させる human-in-the-loop**（ADR-013/027/009）。policy/CORE には自動で触れず、蒸留先を `knowledge_cards` の draft に固定する。

---

## 5. 優先度マトリクス（value×effort）と着手順

| 提案（束） | value | effort | ADR | 依存 | 優先 |
|---|---|---|---|---|---|
| A. 採点台帳＋`score_past_proposals`（P1） | high | L | needs-adr（ADR-014 強化） | なし | ★1（骨格） |
| D-1. FTS5 recall（P6） | high | M | needs-adr（生チャット非索引の線引き） | なし | ★2（安価・独立・即効） |
| B. 蒸留 reviewer FACE（P2/P3/P11） | high | L | 一部 aligns | **A** | ★3（ループを閉じる） |
| C. level=user プロファイル（P4/P7） | high | M | needs-adr（記述↔規範分離） | なし | ★4（ADR-041 実装・独立着手可） |
| D-3. 学習グラフ可視化（P8） | high | L | needs-adr | **A** | ★5（human-in-the-loop 提示） |
| E-1. スケジュール助言 cron（P9） | high | M | needs-adr（ADR-011 隔離） | （採点ウォッチは A） | ★6（器・段階分割） |
| D-2. 会話フォールド層（P5） | medium | M | needs-adr | なし | 保留（用途を昇格 CP に限定） |
| E-2. 分析デリゲート（P10） | medium | L | tension | investigate_stock 重複 | 保留 |

### おすすめ着手順
1. **P1 採点台帳＋`score_past_proposals`**。全ループの前提。ATDD＋新 ADR で「予測 vs 実現台帳・夜バッチ初の backward-looking・outcome→prompt 還流」を正本化。
2. **P6 FTS5 recall** を**並行**（A に非依存・埋め込みコストゼロで journal n=1 帯域を即改善）。
3. **P2/P3/P11 蒸留 reviewer FACE**（A 完了後・活動量ゲート＋過学習足切りを Python で先に固める）。ここで初めてループが閉じる。
4. **P4/P7 level=user プロファイル**（独立着手可・ADR-041 を実装し反追従の土台に）。
5. **P8 学習グラフ**（A の帰結を可視化し剪定を human-in-the-loop に）。
6. **P9 cron**（採点ウォッチを宣言で足せる器へ）。P5/P10 は保留か縮小。

---

## 6. 見送り／注意

### ADR と真っ向反するもの（移植しない／改変して取る）
- **P10 の hermes code_execution 側（LLM が Python を書いて RPC で Tool を叩く）**: ADR-016（手法はテスト済みコード）・ADR-014 に真っ向反する。**literal 移植は禁止**。多段パイプラインは Python の合成 Tool で畳み、LLM は解釈のみ。
- **policy/CORE の自動改変**: どの提案も成績を policy へ自動反映したり CORE を自動改訂してはならない（ADR-013/015/027）。蒸留先は `knowledge_cards` の draft に固定し、weight/active 化・policy 変更は必ず承認ゲート（ADR-009）。**自動チューニングへの滑落が全提案共通の最大の設計リスク**。

### ADR-029（会話揮発）との緊張
- **P6 FTS5 は生チャットを索引しない**線引きを新 ADR で明文化（既に永続する `advisor_journal`＋`proposals` のみ対象）。P5/P11 も生ログを恒久化せず、蒸留メモリ・非破壊射影に留める。

### 単一ユーザー／無人運用／ラズパイ制約で意図的に落とすもの
- hermes の分散インフラ（chronos provider 抽象・JWT webhook・scale-to-zero・self-healing／別プロセス spawn・peer 分離）は単一ユーザー・家庭内 LAN・単一 FastAPI プロセス（ADR-001/002/005）では過剰。常駐 APScheduler 一本・同居プロセス内 DB アクセスに割り切る。
- 蒸留・reviewer・cron の LLM コストは活動量ゲート＋月次コストガードで抑える。ラズパイは推論のみ（ADR-006）。
- Free 12週遅延で実現リターン採点が数週遅れる。「採点不能」を明示し、帰属問題は**超過リターン**で分離。

### 過学習・承認疲労（テーマ B/C 共通）
- 単発トレードを恒久ルールに durable 化しない（反復回数を Python 頻度カウントで足切り）。draft 隔離で単発 LLM 誤分類の被害を限定。`near_duplicate_of` 相当をカードに追加し、頻度・outcome で優先度を付けた少数 draft のみ提示。

---

## 7. 実装ステータス（2026-07-02 時点）

「どこまで実装したか／どこまで検証できたか」の記録。以後着手するたびにこの節を更新する。

### 全体サマリ

| 束 | 内容 | 状態 | 正本 |
|---|---|---|---|
| **テーマ A（★1）** | 提案アウトカム自己採点ループ | ✅ **実装済み・自動＋実機E2E検証済み**（AI 呼出しのみ LLM 未設定で未） | [ADR-077](../docs/decisions.md) |
| **D-1（★2）** | 判断ログ横断想起（FTS5 recall） | ✅ **実装済み・自動＋実機E2E検証済み**（AI 呼出しのみ LLM 未設定で未） | [ADR-078](../docs/decisions.md) |
| **テーマ B（★3）** | 経験蒸留 reviewer 面（採点→カード draft） | ✅ **実装済み**（2026-07-03・自動テスト green／AI 呼出しのみ LLM 未設定で未） | [ADR-081](../docs/decisions.md) |
| テーマ C（★4） | 投資家プロファイル記憶層（独立テーブル `investor_profile`） | ✅ 実装済み（2026-07-03・ADR-082・AI 実呼出し E2E のみ dev LLM 未設定で未） | ADR-082 |
| D-3（★5） | アドバイザー学習グラフ | ⬜ 未着手（A に依存） | — |
| E-1（★6） | スケジュール助言 cron | ⬜ 未着手 | — |
| D-2 / E-2 | 会話フォールド層 / 分析デリゲート | ⏸ 保留（§5 参照） | — |

### テーマ A（採点ループ）＝実装完了の内訳

**確定した設計判断**（①② はユーザー確定・③〜⑧ は推奨で確定）:
- 母集団 = buy/sell 提案（ADR-052）＋ notable_picks（ADR-067）。policy_change/rebalance は対象外。buy/sell は方向性ありで hit、notable は非方向で hit=NULL（リターンのみ記録）。
- entry = 提案日（`created_date`/`date`）以上の**最初の実在バー**の adj_close（forward・休場/未取得なら翌営業日へ・データ未達は pending）。
- horizon = **20 と 60 営業日**の 2 本立て（`horizon` 列で 1 提案=複数行）。未経過は pending で保留し翌晩 final へ。
- 営業日カウント = 株価系列そのもの（N 本先の終値＝到達・別カレンダー非依存）。
- リターン = 絶対＋対ベンチ超過（JP=`^TPX`／US=`^SPX`）を両方保存。ベンチ欠測は excess=NULL＋`benchmark_fallback=1`。
- hit = 超過で判定（buy→excess>0・sell→excess<0）。ベンチ欠測時は絶対リターンの符号にフォールバック。
- AI への戻し = Tool `get_track_record`（pull・min_phase=1）。プロンプトに数字を push しない（ADR-014/025）。CORE 要素④に一文追記。
- **US buy/sell も採点対象**（`us_daily_quotes`＋`get_us_quotes` 既存）。提示ベース評価＝実 P/L ではない。

**追加/変更したファイル**:
- 新規 `backend/app/quant/outcome.py`（`compute_horizon_outcome`／`classify_hit`・DB/today 非依存の純関数）
- 新規 `backend/app/db/repo/proposal_outcomes.py`＋`repo/__init__.py` 配線
- 新規 `backend/app/services/track_record.py`（`score_pending_outcomes`／`get_track_record`）
- 新規 `backend/app/batch/jobs/score_proposal_outcomes.py`＋`NIGHTLY_JOBS` 挿入（`run_advisor` の後・`notify_digest` の直前＝夜バッチ初の backward-looking）
- 新規テーブル `proposal_outcomes`（`schema.py`）＋ migration `0036_proposal_outcomes`
- Tool 追加 `get_track_record`（`tools/schemas.py`＋`handlers.py`＋`registry.py`・min_phase=1）
- CORE `core_prompt.md` 要素④に一文
- docs 同期: `docs/decisions.md`（ADR-077）・`docs/data-model.md`（`proposal_outcomes` 表）・`CLAUDE.md`（migration 0036・ADR 範囲・要約）
- テスト新規 4 本（`test_quant_outcome.py`／`test_track_record_service.py`／`test_score_proposal_outcomes_job.py`／`test_get_track_record_tool.py`）＋ `test_advisor_tools.py` のドリフトガード更新

### 検証できたこと（自動）

- **backend pytest = 1068 passed（0 failed）**。新規 21 ケース＝quant 境界（起点無し／horizon 未到達／非営業日起点 forward／NaN／ベンチ欠測 fallback／entry<=0）・service（pending→final 遷移・US 振り分け・notable 非方向・source 導出 journal/NULL→chat）・job（冪等 UPSERT・部分欠落でも ok=True・pending→final）・tool（集計・JSON-safe・フィルタ・空母集団）。
- **ruff / pyright green**（line-length 100・CJK 幅 2 まで含め通過）。
- **migration を実 SQLite に適用**し `alembic current` = `0036_proposal_outcomes (head)`・テーブル 20 列＋4 index（UNIQUE 自動 index 含む）を確認。
- **起動時 import OK**（`app.main` が registry/handlers/services を全解決）・`NIGHTLY_JOBS` 順序 `run_advisor(20) < score_proposal_outcomes(28) < notify_digest(30)`・`get_track_record` が phase1 で露出。

### 実機 E2E 検証結果（2026-07-02・dev Docker の実 DB）

dev の実 DB（named volume `assetvane-db`・721MB）に実サーバと同じ engine 設定で接続して検証。**採点 math が実市場データで通ることを実証した**（提案が 0 件なので back-dated な buy 提案 1 件〔72030・entry 2026-06-01〕を seed → 採点 → 集計確認 → 撤去し残渣ゼロを確認）。

- **`index_quotes` に `^TPX`／`^SPX` が実在** ✓（^TPX 23 件〔2026-06-01〜07-01〕・^SPX 21 件）。ベンチ欠測 fallback ではなく**実ベンチから超過リターンが出た**（下記）。
- **`score_pending_outcomes` が実 DB でクリーン実行** ✓（upserted 2・finalized 1）。horizon 20＝**final**〔entry 2905.5(06-01)→exit 2772.0(06-29)・realized -4.59%・**excess -5.64%**〔^TPX 実算出・fallback=0〕・**hit=False**（buy で excess<0＝規則どおり）〕／horizon 60＝pending（前方バー不足）。
- **`get_track_record` が実 DB で正しく集計** ✓（source=chat〔journal_id NULL fallback〕・summary hit_rate 0.0・avg_realized -0.0459・avg_excess -0.0564・pending_count 1・as_of 2026-06-29・JSON-safe）。
- **撤去後 proposals=0／proposal_outcomes=0**（seed の残渣なし）。

### まだ検証できていないこと＋実データ所見（運用/実機）

- ⚠️ **LLM face が全未設定・`llm_providers` が空**＝**AI が実際に Tool（`get_track_record`）を呼ぶ E2E は未検証**（チャット/nightly が動かせない）。ハンドラ/サービスは実 DB で実証済みだが「AI が呼ぶ」部分は `/settings` で provider 登録が要る＝運用側の判断。
- ⚠️ **提案の自然蓄積が未達**＝`proposals`/`notable_picks` が 0 件（cron 6/15 停止）。organic な final 集計が育つには pipeline 再稼働＋20/60 営業日待ちが要る。★3（reviewer 面）の教材＝採点済み outcome も同じ理由で実データがまだ薄い。
- ⚠️ **ベンチ履歴が浅い**＝`^TPX`/`^SPX` が 6 月分（〜23 件）のみ。古い entry や horizon=60 の超過リターンは当面 `benchmark_fallback=1`（絶対判定）に倒れる。`fetch_index` のベンチ履歴バックフィルが深まるまで超過は限定的。

### D-1（FTS5 recall）＝実装完了の内訳（2026-07-02・ADR-078）

**確定した設計判断**（grill-me で確定）:
- コーパス = 永続済みの判断ログ 3 ソース（`advisor_journal`＋`proposals`＋`notable_picks`）。**生チャットは非索引**（ADR-029）。索引列 = journal の observations＋proposal／proposals の rationale／notable の reason（JSON 列は生索引しない）。
- 構造 = **統合スタンドアロン FTS5 `judgment_fts(body, origin, ref_id, code, entry_date)`**（trigram・origin 判別列つき）＋各基底表 9 トリガで自動同期。external-content は不採用。
- DDL の**単一真実源** = `app/db/fts.py`（ensure/rebuild/drop）を migration と `create_schema()` の両方から呼ぶ（FTS 仮想表/トリガは metadata に載らずテスト経路で作られない非対称を解消）。
- bookend = proposal/notable ヒットは `proposal_outcomes` を LEFT JOIN し horizon 20/60 の実現/超過リターン＋hit or pending を添える。journal はテキストのみ。`get_track_record`（集計）と個別想起で補完。
- Tool = `search_judgments`（pull・min_phase=1・read-only＝persist なし）。引数 query（必須・3 文字以上）／code／origin／limit。常時注入 n=1 は据え置き（pull 一本＋CORE 一文）。

**追加/変更したファイル**:
- 新規 `backend/app/db/fts.py`（DDL 単一真実源）・`backend/app/db/repo/judgments.py`（`search_judgment_fts`／`search_judgments`＋`repo/__init__` 配線）・`backend/app/services/judgments.py`（3 文字ガード・JSON-safe 整形・例外→空＋理由）。
- Tool 3 分業 `search_judgments`（`tools/schemas.py`＋`registry.py`〔min_phase=1〕＋`handlers.py`）。
- `engine.create_schema()` に `ensure_judgment_fts` を追加。migration `0037_judgment_fts`（rebuild/drop）。
- CORE `core_prompt.md` 要素④に一文。ドリフトガード `test_advisor_tools.py`（phase1/registry 期待集合に `search_judgments`）。
- docs 同期: `docs/decisions.md`（ADR-078）・`docs/data-model.md`（`judgment_fts` 表）・`CLAUDE.md`（0037・ADR 範囲 078）・本 §7。
- テスト新規 3 本（`test_judgments_repo.py`／`test_search_judgments_service.py`／`test_search_judgments_tool.py`）。

**検証できたこと（自動）**:
- **backend pytest = 1082 passed（0 failed）**。新規 14 ケース＝repo（trigram CJK 部分一致・トリガ insert/update/delete 同期・origin/code フィルタ・rebuild 冪等・bm25 順）・service（bookend の final/pending・notable 合流・journal テキストのみ・JSON-safe・3 文字ガード・空母集団）・tool（handler 集計/フィルタ/欠落 error/空）。
- **ruff / pyright green**。
- **migration を実 SQLite に通し検証**：0036 まで上げ既存行を投入 → 0037 head で backfill 3 行 → trigram MATCH（journal/notable）→追加 insert のトリガ同期 → downgrade で仮想表＋9 トリガが完全消去、を確認。

**実機 E2E 検証結果（2026-07-02・dev Docker の実 DB）**:
- **`search_judgments`（サービス）が実 DB でヒット** ✓。`judgment_fts` は journal 由来 2 件を索引済み。trigram の CJK 部分一致で「現金比率」「モメンタム」「方針を更新」がヒットし、snippet ハイライト（《現金比率》）・本文全文・source 中継まで返った。3 文字未満ガード（`方`→空＋理由）も動作。journal ヒットは outcomes キー無し（bookend の設計どおり）。

**まだ検証できていないこと（運用/実機）**:
- ⚠️ **AI が実際に `search_judgments` を呼ぶ E2E は未検証**（LLM face 全未設定＝チャット/nightly が動かせない）。ハンドラ/サービス/FTS 索引は実 DB で実証済み。
- **proposal/notable 起点の bookend（`proposal_outcomes` 合流）は実データ未検証**＝`proposals`/`notable_picks` が 0 件のため journal ヒットしか出せなかった（★1 と同じ「提案未蓄積」がボトルネック）。合流ロジック自体は pytest 済み。

### 次段の着手候補

1. **テーマ B（★3）**: 採点済み outcome を教材に reviewer 面（FACES に triage 同型で 1 面追加）で `propose_card` draft を起票（承認制・過学習足切りを Python で）。A に依存＝ループを閉じる本丸。D-1 の想起（`search_judgments`）を蒸留の材料参照にも使える。
2. **テーマ C（★4・level=user プロファイル）**: A/D-1 に非依存・独立着手可。ADR-041 の土台。

---

## 8. ★3 テーマ B 設計＝**実装済み（2026-07-03・ADR-081）**

**2026-07-03 に実装完了**。下の決定ツリー（Q1〜Q13）は 2026-07-02 に推奨で仮置きしたドラフトで、2026-07-03 にユーザーが Q1・Q3・Q4・Q7 を**すべて推奨どおり承認**（Q1=Tool-calling ターン／Q3=カーソル＋閾値3／Q4=min_samples=3／Q7=既存改訂は v1 スコープ外）。残る Q2/Q5/Q6/Q8〜Q13 は推奨のまま採用。ADR は **ADR-081**（当初「ADR-079 予定」と書いたが 079＝清原式・080＝ウォッチリスト追加で埋まっていたため 081 に確定）。**発火ゲートの「新規 final」は当初案の `as_of_date` でなく `scored_at`（final 化時刻）で数える**に微修正（鮮度遅延で過去日 finalize を取りこぼすため・ADR-081 代替案 C）。実装内訳＝`llm_config.FACES` に `reviewer` 追加（seed migration なし・conftest 自動 seed）／`openai_tools`/`run_tool_loop`/`run_turn` に `tool_names` allowlist＋`REVIEWER_TOOLSET`／repo `count_final_outcomes_since`/`list_new_final_outcomes`/`latest_final_scored_at`/`upsert_fetch_meta_tx`／`services/experience.py`（ゲート件数・素材構築・整形）／`advisor/reviewer.py`（`run_experience_distillation`）／`persist_card_ops_from_tool_runs` に `source_override`／job `distill_experience`（NIGHTLY_JOBS の score→distill→notify）／`notify_digest` の draft N 件行／`knowledge_cards` repo `count_reviewer_drafts_on`。**backend pytest 1131 green・ruff／pyright green・migration 不要（head=0038 のまま）**。**実データ運用（reviewer 面を /settings 登録＋pipeline 再稼働＋AI が実際に Tool を呼ぶ E2E）は ★1・★2 と同じボトルネック（dev は LLM 未設定・提案 0 件）で次回運用時に確認**。以下は設計判断の記録（実装の真実は ADR-081）。

### 既存インフラの確定事実（grounding 済み）

- **FACES** はハードコード tuple（`app/services/llm_config.py:22` = `("chat","nightly","dossier","tagger","triage")`）。reviewer 追加＝tuple に足す＋`llm_face_config` に行を seed する migration。`describe_faces` は FACES を舐めて未設定行も返すので `/settings` に自動で出る（既存テストの FACES 数期待はドリフト更新が要る）。
- **reviewer が使う Tool は全て既存**＝`propose_card`/`adjust_card_weight`/`search_cards`（min_phase=4）・`get_track_record`/`search_judgments`（min_phase=1）。**新 Tool は原則不要**。
- **`run_tool_loop`（service.py:83）は `openai_tools(phase)` で phase の全 Tool を渡す＝tool allowlist 機構は無い**（reviewer の tool 制限は新設が要る）。
- **`persist_card_ops_from_tool_runs`（journaling.py:327・W2）が draft 起票の既存経路**。reviewer は `persist_journal/trade/notable` を呼ばなければ trade/journal 系は永続されない（persist 層が安全境界）。`propose_card` は常に `status='draft'`、`adjust_card_weight` は `proposals(kind='card_weight')` へ承認制起票。
- **nightly.py の骨格**＝`build_messages(CORE, policy, conversation=[instruction], knowledge_cards, recent_journal)` → `run_turn` → `persist_*`。reviewer はこれを踏襲。
- **ジョブ順**（確認済み）: `run_advisor(20)` < `score_proposal_outcomes(28)` < `notify_digest(30)`。

### 決定ツリー（推奨・理由・要レビュー）

| # | 分岐 | 推奨 | 理由 / 代替 |
|---|---|---|---|
| **Q1** | reviewer の呼び出し形態 | **Tool-calling turn（run_turn・reviewer 面）** | 蒸留の核＝「採点済み outcome に grounded」＋「重複カードを作らない」。後者は §4④「search_knowledge_cards 近傍→近ければ…」＝`search_cards` で起票前に近傍検索する規律で、Tool アクセスが要る。単発 JSON だと dedup 規律を失う。既存 run_turn＋persist_card_ops を再利用。コスト/暴走は活動量ゲート＋toolset 制限＋max_rounds で抑える。**（この Q だけ離席で未回答）** |
| **Q2** | tool 制限機構 | **run_tool_loop/run_turn に任意 `tool_names: set[str]\|None` を足し、reviewer は最小集合だけ見せる＋末尾で `persist_card_ops` のみ実行（多重防御）** | propose_trade/submit_journal/submit_notable を見せない＝無駄ラウンド＋誤起票を断つ。persist 層が backstop。allowlist は `openai_tools` を集合で filter する小改修。v1 toolset = `{get_track_record, search_judgments, search_cards, propose_card, adjust_card_weight}`（get_dossier/get_valuation は要れば後追い）。 |
| **Q3** | 活動量ゲート（発火条件） | **カーソル＋閾値**＝`fetch_meta('reviewer:cursor')` に「最後にレビューした as_of_date」を持ち、`as_of_date > cursor` の新規 final outcome 件数が閾値（既定 3・config）未満なら **LLM を呼ばず skip**。成功時のみカーソルを前進。 | hermes `_iters_since_skill` の投資版＝「新しく解決した経験が溜まった晩だけ学ぶ」。新規 final のみ教材化で材料も有界化。full 集計は Tool（get_track_record）で別途引ける。 |
| **Q4** | 過学習の足切り | **Python が教材を「count ≥ min_samples（既定 3）の集計バケット」に絞る＋『単発トレードから durable card 化するな／一般化はバケットを引用せよ』と指示** | 「最小サンプルは LLM に委ねず Python 頻度カウントで」。ハード保証は材料整形段（集計は source×kind×horizon で count≥floor のみ pattern 提示・個別 outcome は文脈のみ）。 |
| **Q5** | 教材の中身 | **①count≥floor の track_record 集計 ②cursor 以降の新規 final（各 outcome を起点 proposal.rationale＋journal.observations/situation_briefing 頭で bookend）③直近 journal 少量。数値は全て Python 計算の verbatim** ＋Tool で追加想起可 | §3B「頭尾を無損失保護し中間 Tool 探索だけ定性要約・数値は verbatim」。bookend＝rationale（頭）→outcome（尾）。生チャットは載せない（ADR-029）。 |
| **Q6** | draft 固定＋source タグ | **reviewer 経路は `source='reviewer'` を決定論で強制（LLM の source 引数を信用しない）・常に status=draft・活性化は人間（ADR-009）** | `persist_card_ops_from_tool_runs` に任意 `source_override` を足し reviewer job が 'reviewer' を渡す（既存 nightly/chat は None で不変）。`/cards` で reviewer 由来 draft を識別・選別できる。 |
| **Q7** | 重複回避 / 既存改訂 | **v1＝search_cards 先行→近傍かつ有効なら adjust_card_weight ↑／近傍だが誤りなら ↓／新規なら propose_card。既存カードの本文改訂は v1 スコープ外（改訂 Tool は作らない）** | §4④「本文改訂 draft」は改訂 Tool 不在ゆえ後回し。near-dup 判定は search_cards（意味検索）＝LLM 判断・Python 自動マージなし（themes 前例）。 |
| **Q8** | ジョブ配置 | **`distill_experience` を `score_proposal_outcomes(28)` の後・`notify_digest(30)` の前（order=29）に挿入** | その晩の final が採点された直後に蒸留。§4④「notify_digest 直前」。 |
| **Q9** | digest フィードバック | **notify_digest に決定論の 1 行「🗂 知識ノート下書き N 件（/cards で確認）」を追加（当夜 draft を作ったとき）** | チャットの card_ids フィードバックと同型・低コスト高可視。件数は persist 戻り値から。 |
| **Q10** | コスト/予算 | **既存コストガード（ADR-028 `llm_usage`）＋活動量ゲートに委ね、新予算機構は作らない** | reviewer も `complete()` 経由でガード対象。ゲートが頻度を抑える。 |
| **Q11** | 障害時（ADR-018） | **face 未設定＝沈黙 skip（ok=True・triage/tagger 同型）／LLM ハード失敗＝job が ok=False で runner 集約通知（非致命・後続と digest は止めない）。skip/失敗時はカーソルを前進させない** | 学習ジョブは日次運用に非critical＝未設定を nag しない。設定済みで落ちるのは実障害なので surface。材料を失わないためカーソル保持。 |
| **Q12** | ADR＋docs 同期 | **ADR-079（reviewer 面/経験蒸留）。同期＝decisions.md・advisor.md（reviewer 面・FACES 6 化）・data-model.md（`reviewer:cursor` メタキー・source='reviewer' 値）・CLAUDE.md・roadmap.md・本 §7/§8** | FACES 6 化＋`llm_face_config` seed migration。/settings は自動露出。 |
| **Q13** | 「レビュー済み」状態 | **新列/新表を作らず `fetch_meta('reviewer:cursor')` の日付カーソル 1 本**（edinet:crawl 前例） | スキーマ増やさず・材料有界化とゲートを同時に満たす。 |

### ATDD テスト叩き（実装時に先行）

- reviewer 面の resolve/gate（未設定→沈黙 skip・ok=True）。
- 活動量ゲート＝新規 final < 閾値で LLM を呼ばず skip／≥閾値で発火。
- 材料整形＝count≥floor のバケットのみ pattern 提示・cursor 以降の新規 final を選択・数値 verbatim。
- tool allowlist＝reviewer に見える Tool は 5 本のみ・propose_trade/submit_journal は不在。
- persist＝source が 'reviewer' に強制・status=draft・adjust_card_weight→card_weight 提案。
- カーソル＝成功で前進・skip/失敗で不変。
- ジョブ＝冪等・ok フラグ・順序（score の後・digest の前）。
- digest＝draft 作成時に 1 行出る。
- ドリフトガード＝`test_advisor_tools`（reviewer toolset）＋FACES 6 化の期待更新。

### 実装前に潰す確認事項（→ 離席中にコードで裏取り済み）

- ✅ **`llm_face_config` の seed 方式**＝**seed migration は無い**。本番は `/settings`（`PUT /llm/faces/<face>`＝`repo/llm_config.py:127` の upsert）でオンデマンド生成、テストは `conftest.seed_llm_config` が `for face in FACES` で全面自動 seed（`conftest.py:54`）。**reviewer は FACES tuple に足すだけ**で `/settings` 露出（`describe_faces` が FACES を舐める）＆テスト seed が効く。未設定面は `get_face`→None→`FaceNotConfiguredError`→沈黙 skip（ADR-058「シードなしゆえ初回は /settings 登録まで動かない」と整合）。
- ✅ **FACES 集合を assert する既存テスト**＝`test_llm_config.py:72`（`{"chat","nightly","dossier","tagger","triage"}` のハードコード集合）1 箇所。ここに 'reviewer' を足す。conftest は `for face in FACES` で自動追従するので他テストの seed は無改修。
- ✅ **`fetch_meta` ヘルパ**＝`get_fetch_meta(conn, source)`／`upsert_fetch_meta(source, last_fetched_date)` が既存（`repo/stocks.py:109/142`）。`source='reviewer:cursor'` で流用可（`edinet:crawl` と同じキー方式）。
- ⚠️ **`run_turn` の allowlist 追加**は要実装確認＝デフォルト `tool_names=None` で全 Tool（chat/nightly 現状維持）、reviewer だけ集合を渡す。`run_tool_loop` の `openai_tools(phase)` を集合で filter する小改修（複数箇所で `openai_tools` を呼ぶので filter は 1 関数に寄せる）。

### 大きな含意（レビュー時の要点）

- **★3 テーマ B は migration 不要の見込み**＝(1) reviewer 面は FACES tuple 追加のみ（seed migration なし）(2) カーソルは既存 `fetch_meta` 流用 (3) `source='reviewer'` は `knowledge_cards.source` の文字列値（スキーマ非改変）。**新テーブル/新列ゼロ**で ★1・★2 より低リスク。主要な新規コードは①reviewer 面の指示文＋job `distill_experience` ②材料整形サービス（集計 floor＋新規 final 抽出）③`run_turn` の tool allowlist ④`persist_card_ops` の source_override ⑤notify_digest の 1 行＋活動量ゲート、に限られる。
