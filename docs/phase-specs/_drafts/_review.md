# 横断レビュー（adr-guardian / タスク #6）

> アーキ番人・批判役による 4 ドラフト横断レビュー。
> 対象: `_drafts/{data-arch,quant,app,ai-advisor}.md`・接地は `_current-state.md`・規律は `decisions.md` 全 ADR。
> 作成: 2026-06-03。**コードは書かず・git/jj 触らず・過去コミット不可侵。書き込みは本ファイルのみ。**
> 指摘は「該当ファイル §＋問題＋是正案」の形。重大度は 🔴致命（R3 で必ず直す）/ 🟡要調整 / 🟢確認のみ。

---

## 結論サマリ（lead 向け先読み）

- **ADR 不変条件の違反は「なし」**（4 レーンとも ADR をよく踏んでいる）。ただし ADR-002（書き手 1 つ）に関し ai-advisor が `policy`/`proposals` を昼にも書く設計で、data-arch の「夜バッチ 1 プロセス」と**運用前提の整理が要る**（B-9・違反ではないが明文化必須）。
- **レーン間の致命的不整合が 2 件**: ① **Alembic リビジョン番号の衝突（data-arch `0002`=fetch_meta vs quant `0002`=signals）**、② **Tool 返却スキーマ（関数名・キー）が quant ⇔ ai-advisor で広範に不一致**。両方とも R3 で必ず収束させる。
- **ratio 単位（0..1 / %）**は app が「DB/API=0..1・UI のみ %」と明言。quant/ai-advisor も 0..1 で概ね整合だが、**app の API レスポンス型（`weight`/`current_weight`/`target_weight`/`delta`/`pct`/`deviations.current,limit`）のコメントが「%」表記**で、横断ルールと自己矛盾している（B-3・要統一）。
- **破壊的変更・作り直し: なし**（既存 Phase 0 実装を尊重。`backfill.py` は「残置＋薄いラッパ化」で互換維持）。
- **R3 要否**: data-arch=要（軽微）/ quant=要（リビジョン番号・Tool キー）/ app=要（単位表記・リビジョン参照）/ ai-advisor=要（Tool キーを quant に合わせる）。**全レーン R3 あり、ただし大半は機械的収束**。
- **ユーザー裁定必須 [OPEN] は 9 件**（後述 F）。残りは lead 裁量で可。

---

## A. ADR 適合チェック表

| ADR | 規律 | data-arch | quant | app | ai-advisor |
|---|---|---|---|---|---|
| 001 | 単一ユーザー・user_id 無し | ✅（portfolio_id は器のみ） | ✅ | ✅ | ✅（発注しない明記） |
| 002 | SQLite WAL・書き手 1 プロセス | ✅（flock＋max_instances=1 で担保） | ✅（signals UPSERT 冪等） | 🟢（書きは API 経由） | 🟡 **B-9**: policy/proposals を昼に書く前提を明文化要 |
| 005 | DB は FastAPI のみ・Next 非接触 | ✅ | ✅（quant は DB を知らない純関数） | ✅（lib/api.ts のみ・Prisma 無し） | ✅ |
| 006 | 学習は別 PC・ラズパイ推論のみ | ✅（.pkl scp・models/ 管理外） | ✅（train.py は別 PC・infer のみ） | — | — |
| 007 | 通知は Discord | ✅（DiscordAdapter） | — | ✅（Webhook は .env 固定） | ✅（エラー通知 Discord） |
| 008 | J-Quants V2・Free 12週遅延 | ✅（既存 x-api-key 流用） | ✅（遅延は鮮度問題と整理・is_delayed） | ✅（valuation_meta） | ✅（delayed 添える規律） |
| 010 | アダプタ越し | ✅（IndexAdapter/Discord も adapters/） | 🟢（数理は adapters 不要） | — | ✅（fetch_news は data 委譲） |
| 011 | 1 つの脳・2 つの起動口 | ✅（run_nightly 共用） | — | — | ✅（build_messages 共通・nightly 相乗り） |
| 012 | LLM アダプタ・OpenRouter 既定 | ✅（timeout/retry 追記） | — | — | ✅（complete 拡張・既定維持） |
| 013 | policy 単一・版管理なし | ✅ | 🟢（policy を制約に写像のみ） | ✅（PUT で 1 行更新） | ✅（snapshot で履歴・proposals 承認制） |
| 014 | AI は計算しない | 🟢（バッチは糊・計算は quant 委譲） | ✅（純関数が事実を計算） | ✅（deviations は Python 計算） | ✅（Tool 戻り値のみ・facts 静的に積まない） |
| 015 | CORE 不変＋POLICY 可変 | 🟢 | — | — | ✅（core_prompt.md 起動時読込・チャット不可触） |
| 016 | 手法はコード・AI に計算させない | 🟢 | ✅（自前実装＋既知系列テスト・再現性） | — | ✅（method_cards は索引・計算なし） |
| 017 | DB 定期バックアップ | 🟢（言及なしだが範囲外） | — | — | — |
| 018 | 無人障害・失敗を放置しない | ✅（fetch_meta 再開・Discord） | 🟢（推論失敗スキップ） | — | ✅（journal スキップ＋通知） |
| 019 | holdings は transactions 導出 | ✅（holdings は導出値・直接編集しない） | — | ✅（POST /transactions 経由） | 🟢（buy 承認は約定しない） |
| 020 | ドシエ DB 保存・本文捨てる | — | — | ✅（Dossier 型・sources 要約のみ） | ✅（本文渡さず・URL UNIQUE 重複排除） |
| 021 | Docker Compose・ARM 注意 | ✅（cron 方式 C で追加コンテナ 0） | 🟡 **B-10**: ARM ビルド実機確認が未済（gate） | ✅（standalone 前提・依存追加注記） | — |
| 022 | Next ビルド Turbopack | — | — | ✅（dev=turbopack 維持） | — |
| 023 | uv/Ruff/pyright・Biome | ✅ | ✅ | ✅ | ✅ |
| 024 | チャット常駐・遷移で保持 | — | — | ✅（root layout 維持・localStorage） | ✅（常駐前提） |
| 025 | 画面 context 軽量・数値載せない | — | — | ✅（数値載せない・focus は対象のみ） | ✅（軸1 は context=None 強制） |

> **総評**: ADR 違反（破る記述）は無い。🟡 は「ADR は守るが、レーン間で前提合わせ／実機確認が要る」もの。

---

## B. レーン間契約の不整合リスト（名指し・是正案）

### 🔴 B-1. Alembic リビジョン番号の衝突（致命）

- **data-arch §1.1**: `fetch_meta` を `alembic/versions/0002_fetch_meta.py` に割当。
- **quant §1.4 / §1.8**: `signals` を `Alembic 0002_signals` に割当。
- **問題**: 同じ `0002` を 2 レーンが別テーブルに使っている。Alembic は単線リビジョン（`down_revision` チェーン）なので**番号衝突＝適用不能**。
- **是正案**: Phase 1 のリビジョン順序を lead が一意に確定する。推奨: `0002_fetch_meta`（data-arch）→ `0003_signals`（quant）。以降 app/data-arch/ai-advisor の後続も**1 本のチェーンに通し番号**で並べ直す（data-arch は `0003_portfolio_and_assets`/`0004_advisor_state`/`0005_notifications` と既に振っているが、quant の signals が割り込むため**全体の採番表を lead が 1 枚作る**）。**[DOCS要修正＆R3]** data-arch・quant 両方のリビジョン番号を書き換え。

### 🔴 B-2. Tool 返却スキーマ（関数名・キー）が quant ⇔ ai-advisor で不一致（致命）

quant §1.6/§2.2/§2.3 の返却 dict と、ai-advisor §5.3 の Tool 返却 JSON を突き合わせると**広範にズレている**。Tool 契約は「ai-advisor が呼ぶ・quant が供給」なので、**両者で 1 つの正本に揃える**必要がある。

| Tool | quant 側（供給） | ai-advisor 側（消費） | ズレ |
|---|---|---|---|
| `get_indicators` | `{code, date, sma25, sma75, rsi14, vol_ma20, adj_close}`（§1.6） | `{code, date, sma?: {5,25,75}, rsi?, volume?, volume_avg?, delayed}`（§5.3） | 🔴 **sma の形が違う**（quant=平坦 `sma25/sma75` / ai=ネスト `sma:{5,25,75}`）。`sma5` は quant に無い。`rsi14` vs `rsi`、`vol_ma20` vs `volume_avg`、`delayed` の有無。 |
| `get_signals` | `list[{date,code,signal_type,score,payload}]`（§1.6 表）。app は `{date, signals:[...]}` でラップ（P1-1） | `{date, signals:[{code,signal_type,score,payload}]}`（§5.3） | 🟡 トップレベルが「配列」か「`{date,signals}`」か。app/ai は `{date,signals}`。**quant の表記を `{date, signals:[...]}` に合わせる**。signal 要素に `date` を含むか（重複）も統一。 |
| `screen_stocks` | `list[{code, company_name, signal_type, score, payload}]`（§1.6） | `{date, items:[{code, company_name, signal_type, score, indicators?}], delayed}`（§5.3） | 🔴 **`payload` vs `indicators?`**・配列直返し vs `{date,items,delayed}` ラップ。criteria キーも `sector33_code`（quant）vs `sector`（ai）でズレ。 |
| `get_portfolio_metrics` | `{as_of, annual_return, annual_volatility, sharpe, max_drawdown, correlation:{code:{code:val}}, lookback_days, is_delayed}`（§2.2） | `{portfolio_id, correlation:[[…]], sharpe, max_drawdown, deviations?, delayed}`（§5.3） | 🔴 **correlation の形が違う**（quant=ネスト dict / ai=2 次元配列 / app=`{codes,labels,matrix}`＝**3 者三様**）。`is_delayed` vs `delayed`。`deviations` は ai/app にあり quant の metrics 返却には無い。`portfolio_id` の有無。 |
| `optimize_portfolio` | `{as_of, objective, weights:{code:w}, cash_weight, expected_annual_return, expected_annual_volatility, expected_sharpe, constraints_applied, infeasible}`（§2.3） | `{portfolio_id, weights:[{code,target_weight,current_weight}], expected_return, expected_vol, constraints_applied}`（§5.3） | 🔴 **weights が dict（quant）vs 配列（ai/app）**。`expected_annual_return` vs `expected_return`。app は `target_weights/current_weight/delta` でさらに別名。`infeasible`（quant）の扱いが ai/app に無い。 |
| `get_asset_overview` | （quant 単独では未定義・data/quant 折半） | `{total_value, stock_value, cash_value, external_value, allocation:[{code,weight}], pnl, history?, deviations?, delayed}`（§5.3） | 🟡 app の `AssetOverview`（P2-7）は `allocation:[{name,value,pct}]`＋`policy_targets`＋`deviations:[{kind,label,current,limit,breached}]`＋`trend`。**ai の allocation（code 単位）と app の allocation（株式/現金/投信の name 単位）が別物**。deviations の形も別。 |
| `get_financials` | （quant 範囲外・data 供給） | `{code, items:[{disclosed_date, fiscal_period, net_sales, operating_profit, profit, eps, bps}]}`（§5.3） | 🟢 data レーンと突き合わせ要（financials DDL は data-arch P2/P5 で取得予定だが**本ドラフト群に financials の DDL が無い**＝B-7）。 |

- **是正案（R3）**: lead が「Tool 返却スキーマの正本」を 1 枚に固定し、quant・ai-advisor・app の 3 ドラフトを**そこへ揃える**。特に決め事:
  1. **delayed フラグ名**: `is_delayed`（app 全体が `is_delayed`／`valuation_meta.is_delayed`）か `delayed`（ai/quant Tool）か → **`is_delayed` に統一**推奨（app の REST 型が広く `is_delayed`）。Tool 返却も `is_delayed`。
  2. **correlation 形**: app の `{codes, labels, matrix[][] }`（UI 直結・順序保証）を**正本**にし、quant の metrics 返却もこの形へ。
  3. **weights 形**: app の `[{code, current_weight, target_weight, delta}]`（配列・UI 直結）を正本。quant の dict 返却は handler/ルータで配列へ変換 or quant 側を配列に。
  4. **get_indicators の sma**: ネスト `{5,25,75}` か平坦 `sma25/sma75` か。quant の実装（25/75 のみ・sma5 無し）が真実なので **平坦 `sma25/sma75`＋必要なら `sma5` を追加するか決める**。ai 側の `{5,25,75}` 期待は quant に sma5 が無いので**要すり合わせ**。

### 🟡 B-3. ratio 単位の自己矛盾（app 内）

- **app §0（横断ルール）・末尾サマリ**: 「DB・API は 0..1（比率）、UI 表示時のみ %」と明言。
- **app §P2-2 `Holding.weight`**: コメント「株式内の比率（**%**）」。§P2-6 `current_weight`/`target_weight`「現状比率（**%**）」「最適比率（**%**）」、§P2-7 `Deviation.current=18.2`/`limit=15`（%値）、`AllocationSlice.pct`。
- **問題**: app 自身の横断ルール（API=0..1）と、各 API 型のコメント（%）が矛盾。quant（§2.3 weights は 0..1）・ai-advisor（policy 0..1）は 0..1 側。
- **是正案（R3・app）**: app の P2-2/P2-6/P2-7 の `weight`/`current_weight`/`target_weight`/`delta`/`pct`/`deviations.current,limit` のコメントを**全て 0..1** に直す（UI で ×100）。`Deviation` の例値も `current:0.182 / limit:0.15` に。**横断で 0..1 を貫く**（policy が最適化制約と二重活用＝ADR-013 のため、ここが %/0..1 で割れると optimize に渡す値がバグる）。

### 🟡 B-4. /chat context.focus の型不一致

- **app §P3-5 `ChatContext.focus`**: `{ type: "stock"; code: string }`（stock のみ・code 必須）。
- **ai-advisor §4.1 `FocusRef`**: `{ type: stock|portfolio|signal|proposal, code?: str, id?: int }`（type で code/id 使い分け）。
- **api.md §4 現行**: `focus?: { type, code }`。
- **問題**: app は stock+code に絞り、ai は 4 type＋id まで拡張。両者 `/chat` の同じ body を指すのに型が違う。
- **是正案**: ai-advisor の `{type, code?, id?}`（portfolio/proposal は code を持てない）を**正本**にし、app §P3-5 と api.md §4 を拡張（ai-advisor §14・app の [OPEN-F] 近辺と一致させる）。**[DOCS要修正]** api.md §4 を `focus: { type, code?, id? }` に。**R3（app）**: `ChatContext.focus` を ai に合わせて拡張。

### 🟡 B-5. tool 実行可視化フィールド名の不一致

- **app §P3-5**: `ChatResponse.tool_runs?: ToolRun[]`（`{name, args?}`）。
- **ai-advisor §6 / §5.5**: `ChatResponse.tool_calls_made: list[str]`（名前だけ）。
- **問題**: 同じ `/chat` レスポンスのフィールド名・構造が違う（`tool_runs:[{name,args}]` vs `tool_calls_made:[str]`）。
- **是正案（R3）**: どちらかに統一。**推奨: `tool_runs: [{name, args?}]`（app 案）**——UI で「⚙ get_signals 実行」に引数も出せて screens.md §4 に厚い。ai-advisor §5.5/§6 をこれに合わせる（結果値は載せない点は両者一致）。

### 🟡 B-6. signals レスポンスの date 二重・JOIN 主体

- **quant §1.4 DDL**: `signals` に `company_name` 列なし（正しい）。
- **app §P1-1**: `Signal.company_name` は「signals JOIN stocks で補完」（ルータ側）。`Signal` 要素に `date` も持つ。
- **ai-advisor §5.3 get_signals**: 要素は `{code, signal_type, score, payload}`（date 無し）。
- **問題**: 行レベルに `date` を持つか（app=持つ／ai=持たない）、`company_name`/`label`/`change_5d` を誰が足すか（app=ルータ JOIN／quant payload）が三者で微妙にズレ。
- **是正案**: 行レベル `date` は冗長（トップに `date` があるなら不要）だが害は小。**company_name はルータ JOIN（app 案）で確定**（quant の signals DDL に名前を持たせない＝正しい）。`label`/`change_5d` の生成元（quant payload か app ルータか）を P1 着手前に quant↔app で 1 行確定。🟢 害は小・R3 で 1 行明記。

### 🟡 B-7. financials の DDL が宙に浮いている

- **quant §5.2 / ai-advisor §5.3 `get_financials`**: `financials`（`net_sales/operating_profit/profit/eps/bps/disclosed_date/fiscal_period`）を**利用**する前提。
- **data-arch**: `fetch_meta` の source 例に `'financials'` を挙げるが、**`financials` テーブルの DDL・取得ジョブ・取得方法を定義していない**（Phase 2/5 で「取得」と一言あるのみ）。
- **問題**: data-model.md §2 に financials はあるが、**どの Phase でどの DDL・どのアダプタメソッドで取るか**が data-arch ドラフトに落ちていない。quant P5（AI Alpha）と ai-advisor G（ドシエ）が前提にするのに供給仕様が無い。
- **是正案（R3・data-arch）**: data-arch に **`financials` の DDL（data-model.md §2 準拠）＋ J-Quants V2 財務エンドポイント（`/v2/equities/statements` 等・要 jquants.md 確認）＋取得ジョブ（`fetch_financials`）＋ fetch_meta source** を Phase 2 か Phase 5 のどちらで入れるか明記。**[DOCS要修正]** jquants.md 要再確認リストに「V2 財務（statements）エンドポイント」を追加（data-arch §6 の指数/master 追加要望と同じ枠）。

### 🟡 B-8. proposals.depends_on 列の要否

- **app §P3-3 [OPEN-E]**: `proposals.depends_on: number | null` を**追加提案**（policy_change→buy の承認順制御）。
- **ai-advisor §9 proposals DDL**: `depends_on` 列**なし**。data-model.md §5 proposals も無し。
- **問題**: app が UI で依存制御したいが、DDL を持つ ai-advisor/data-model に列が無い。
- **是正案**: **[OPEN・ユーザー裁定 or lead 裁量]**。推奨: lead 裁量で **`depends_on INTEGER NULL`（FK→proposals.id）を proposals DDL に追加**（app の要望は妥当・低コスト）。採用なら ai-advisor §9・data-model.md §5・data-arch の移行発行に反映。**R3（ai-advisor）**: 採用なら DDL に 1 列追加。

### 🟡 B-9. ADR-002「書き手 1 プロセス」と policy/proposals の昼書き込み（前提の明文化）

- **data-arch §0**: 「DB 書き手は夜間バッチ 1 プロセスに限定。画面操作の手入力は昼・人間主導の単発で時間帯衝突しないため許容」と整理。
- **ai-advisor §9**: `policy`/`proposals` は「チャット/承認でも書く（data-model.md で WAL 許容と明記済）」。
- **問題**: ADR-002 の文言は「書き手は夜間バッチ 1 つ」。実際は (a) 夜間バッチ、(b) 昼の手入力（transactions/cash/external_assets）、(c) チャット/承認（policy/proposals/dossiers）の**3 系統が書く**。違反ではない（WAL は書×読 OK・書×書は時間帯分離＋単発で回避）が、**「ADR-002 をコードでどう担保するか」が data-arch のロック（flock）だけでは（b）（c）をカバーしない**（flock はバッチ専用）。
- **是正案（R3・data-arch＋lead）**: data-arch §1.8 のロックは**バッチの相互排他**（cron 重複・手動 batch 競合）であって、昼の API 書き込みとバッチの**同時刻衝突**は別問題と明記。推奨: 「昼の API 書き込みは単発・短時間 UPSERT で、夜間バッチ実行中（lock 保持中）は **API 書き込みも 409 で弾く or リトライ**」までは作らず、**運用規律（夜間帯はユーザーが触らない）＋ SQLite の busy_timeout 設定**で実務上回避、と lead が ADR-002 の解釈を 1 段落補足。**[DOCS要修正]** ADR-002 or data-model.md に「書き手の 3 系統と衝突回避の実際」を補注。

### 🟢 B-10. ARM ビルド実機確認の責任分界（quant ⇔ data-arch）

- **quant §1.1/§2.1/§5.1**: numpy/pandas/scipy/PyPortfolioOpt(cvxpy)/lightgbm の **ARM wheel 実機確認**を要求。
- **data-arch 横断**: 「数理依存は quant 担当」と明記し、ARM クロスビルド確認も quant に投げ返す箇所あり。
- **問題**: ADR-021 の「イメージは別 PC でクロスビルド」はインフラ（data-arch/lead）の責務、依存選定は quant。**実機確認の主体**が両ドラフトで相互に押し付け気味。
- **是正案**: lead が「依存の追加判断＝quant、Docker クロスビルド検証の段取り＝data-arch」と分界を 1 行確定。Phase 1 着手の**最初のゲート**として明記（_current-state.md §設計注意 2 と整合）。

### 🟢 B-11. /batch/run のレスポンス契約（data-arch ⇔ app）

- **data-arch §1.6**: `BatchRunRequest{full_backfill}` / `BatchRunResponse{started, jobs}` / 非同期 202＋409。
- **app §P6-1**: `POST /batch/run` body `{tasks?: string[]}` / res `{started, job_id?}`。
- **問題**: body が `full_backfill`（data-arch）vs `tasks[]`（app）、res が `jobs`（data-arch）vs `job_id`（app）でズレ。
- **是正案**: data-arch を正本（バッチ実体側）に。推奨 body `{full_backfill?: bool}`（`tasks[]` は YAGNI・Phase 1 は全ジョブ実行のみ）、res `{started: bool, job_id?: str}`（非同期 202）。**R3（app）**: §P6-1 を data-arch に合わせる。

### 🟢 B-12. asset_snapshots / deviations の計算所在

- **data-arch §2.5**: `snapshot_assets` ジョブが「評価額の計算は app/quant の導出関数を呼ぶ」。
- **app §P2-7**: `deviations` は「Python が計算・`/asset-overview` に含める」。
- **ai-advisor §5.3**: `get_asset_overview`/`get_portfolio_metrics` のどちらに deviations を載せるかで揺れ（ai は「metrics に載せる」推奨）。
- **問題**: deviations を **`/asset-overview`（app）か `get_portfolio_metrics`（ai）か**で割れている。
- **是正案**: 用途で割り切る。推奨: **画面（Dashboard 資産概要）は `/asset-overview.deviations`、Tool（AI のリスク文脈）は `get_portfolio_metrics.deviations`** の**両方に同じ Python 関数（quant）が供給**（計算は 1 か所・出力先 2 つ）。screens.md §6(b) の宿題は app の「`/asset-overview` に含める」で確定しつつ、Tool 側にも同値を載せる。**R3（quant）**: deviations 計算関数を 1 本化し両者に供給する旨を明記。

---

## C. 破壊的変更 / 作り直しの監視結果

- **既存 Phase 0 実装（stocks/daily_quotes/JQuantsAdapter/既存 5 ルート/lib/api.ts 既存型）を作り直す記述: なし。** 4 レーンとも「差分で積む」「既存流儀を踏襲」を明言。
- **`backfill.py`**: data-arch §1.6 が「後方互換で CLI 残置＋`run_nightly(full_backfill=True)` の薄いラッパに寄せる」＝**作り直しでなく温存**。✅
- **`fetch_daily_quotes_by_date`**: 既存部品をバッチが呼ぶ（新規呼び出し元を足すだけ）。✅
- **既存型（Stock に updated_at 無し・Quote に code 無し・volume は Float・Message は user/assistant のみ）**: app §0.2 が「変更しない」と明記。✅
- **過去コミット改変の示唆: なし。** どのドラフトも「新規リビジョン／新規ファイル／既存に追記」で、履歴書き換えの記述は無い。✅
- **schema.py の重複定義**: `signals`（quant）・`fetch_meta`/`portfolios`/`holdings`/…（data-arch）・`policy`/`advisor_journal`/`proposals`（ai-advisor）・`watchlist`（data-arch §2.1 と ai-advisor §11 の両方に登場）。→ **B-13 参照**。

### 🔴 B-13. `watchlist` テーブルを 2 レーンが別定義（重複）

- **data-arch §2.1（Phase 2）**: `watchlist`（`id/code/note/added_at/UNIQUE(code)`）を `0003` で追加。
- **ai-advisor §11（Phase 4）**: `watchlist`（`id/code/note/added_at`）を Phase 4 で追加。
- **問題**: 同じテーブルを **Phase 2（data-arch）と Phase 4（ai-advisor）の両方で「追加」**。二重定義＝二重 CREATE で移行が壊れる。
- **是正案（R3）**: **watchlist の DDL・移行は 1 レーン 1 Phase に確定**。推奨: roadmap では watchlist は **Phase 4（ドシエと同時）**が自然だが、data-arch は Phase 2 の `0003` に入れている。**lead が「watchlist は Phase 4・ai-advisor 管轄」または「Phase 2・data-arch 管轄」のどちらかに寄せ**、もう一方のドラフトから削除。app §P4-1 が watchlist API を Phase 4 に置くので、**Phase 4・1 か所に寄せる**のが整合（data-arch §2.1 から watchlist を外す）。

---

## D. 各レーンへの要修正リスト（R3）

### data-arch（#2）
- 🔴 **B-1**: Alembic 採番を lead の通し番号表に合わせる（`0002_fetch_meta`→signals が `0003` で割り込む等）。`0003_portfolio_and_assets` 以降の番号を採番表に再整列。
- 🔴 **B-13**: §2.1 の `0003` から `watchlist` を外す（Phase 4・ai-advisor に寄せる）。
- 🟡 **B-7**: `financials` の DDL＋取得ジョブ（`fetch_financials`）＋ jquants V2 statements エンドポイント確認を Phase 2 or 5 に明記。
- 🟡 **B-9**: §1.8 のロックは「バッチ相互排他」と明記し、昼 API 書き込みとの衝突回避（busy_timeout＋運用規律）を 1 段落追記。
- 🟢 **B-11**: §1.6 の `/batch/run` 契約を正本として app に通知（app が合わせる）。
- 🟢 **B-10**: ARM クロスビルド検証の段取り（data-arch 主体）を明記。

### quant（#3）
- 🔴 **B-1**: `signals` のリビジョンを `0002_signals`→**`0003_signals`**（lead 採番表）に修正。
- 🔴 **B-2**: Tool 返却スキーマ（`get_indicators`/`get_signals`/`screen_stocks`/`get_portfolio_metrics`/`optimize_portfolio`）を**正本に揃える**。特に correlation 形（→`{codes,labels,matrix}`）・weights 形（→配列）・`is_delayed` 名・sma 形（25/75 平坦・sma5 の有無を ai と確定）。
- 🟡 **B-3**: §2.3 の weights が 0..1 である旨を再確認（app の % 表記是正と整合）。0..1 で正しいので変更不要だが、app の混乱源を断つため「単位 0..1」を明記。
- 🟢 **B-12**: deviations 計算関数を 1 本化し `/asset-overview` と `get_portfolio_metrics` の両方に供給する旨を明記。
- 🟢 **B-6**: signals payload の `label`/`change_5d` を quant が出すか app ルータが出すかを 1 行確定（app と）。

### app（#4）
- 🟡 **B-3（最重要・app 内自己矛盾）**: P2-2/P2-6/P2-7 の `weight`/`current_weight`/`target_weight`/`delta`/`pct`/`deviations.current,limit` を**全て 0..1** に統一（コメント・例値を直す）。横断ルール（API=0..1）に合わせる。
- 🟡 **B-4**: `ChatContext.focus` を `{type, code?, id?}`（ai-advisor 正本）に拡張。
- 🟡 **B-5**: `ChatResponse` のフィールドを ai-advisor とどちらかに統一（推奨 `tool_runs`）。
- 🟢 **B-11**: §P6-1 の `/batch/run` を data-arch 契約（`full_backfill`/`started,job_id`）に合わせる。
- 🟢 **B-2（correlation/weights）**: `CorrelationMatrix{codes,labels,matrix}`・`OptimizeWeight[]` は UI 直結で良案。これを正本として quant に守らせる（app は維持・quant が合わせる）。

### ai-advisor（#5）
- 🔴 **B-2**: §5.3 Tool 返却スキーマを quant の実装値・app の REST 型と揃える。特に `get_indicators.sma`（ネスト `{5,25,75}`→quant は 25/75 平坦・sma5 無し）・`screen_stocks`（`indicators?`→`payload`／criteria キー `sector`→`sector33_code`）・`delayed`→`is_delayed`・correlation 形・weights 配列形。
- 🟡 **B-13**: `watchlist` を ai-advisor §11（Phase 4）の正本とし、DDL を確定（data-arch から外す前提）。
- 🟡 **B-8**: `proposals.depends_on` 採用なら §9 DDL に 1 列追加。
- 🟢 **B-5**: `tool_calls_made`→`tool_runs`（app 案採用時）。
- 🟢 **B-9**: §9 の policy/proposals 昼書き込みが ADR-002 の例外であることを data-arch の補注と整合させる。

---

## E. [DOCS要修正] 集約一覧（重複排除・lead 統合用）

| # | 対象 docs | 内容 | 出所ドラフト |
|---|---|---|---|
| DOC-1 | data-model.md §6 `fetch_meta` | `updated_at` 列を追記（運用観測・冪等証跡） | data-arch |
| DOC-2 | data-model.md §4 `signals` | `(date,code,signal_type)` UNIQUE を追記（冪等・現状 PK は id のみ） | quant |
| DOC-3 | jquants.md §6 要再確認リスト | (5)`/v2/equities/master` の code 無し全件取得可否、(6) V2 取引日カレンダー API 有無、(7) V2 主要指数(TOPIX/日経) API 有無、**(8) V2 財務(statements) エンドポイント**（B-7 で追加） | data-arch＋guardian |
| DOC-4 | api.md §4 `/chat` | body に `context` を明記＋`focus: {type, code?, id?}` に拡張（現 docs は code のみ・実装時確定止まり） | app（D-1）＋ai-advisor（§14） |
| DOC-5 | screens.md §6(b) | 逸脱計算の置き場所を「`/asset-overview.deviations`（画面）＋`get_portfolio_metrics.deviations`（Tool）の両方に Python が供給」で確定（B-12） | app（D-2）＋ai-advisor |
| DOC-6 | api.md §7 | `/quotes`・`/journal` ページネーション「当面なし（範囲指定で代替）」を明記 | app（D-3） |
| DOC-7 | api.md §7 | `GET /policy` の core/rationale 分離レスポンス形を確定（既存宿題の解消） | app（D-4） |
| DOC-8 | data-model.md §2 / jquants.md | `financials` の DDL 確認＋ V2 statements 取得仕様（B-7） | guardian（新規） |
| DOC-9 | ADR-002 or data-model.md | 「書き手の 3 系統（夜バッチ／昼手入力／チャット承認）と衝突回避の実際（WAL＋時間帯分離＋busy_timeout）」を補注（B-9） | guardian（新規） |
| DOC-10 | data-model.md §4 signals / §0.1（adj_close） | adapter は `adj_close` のみ保存で high/low 系指標（ATR/ストキャス）が当面組めない点を明記（調整 OHLV を足すか後回しか） | quant |
| DOC-11 | data-model.md §5 proposals | `depends_on` 列を追加するか（B-8・採用時のみ） | app |
| DOC-12 | roadmap / data-model.md | `watchlist` の所属 Phase を 1 つに確定（B-13） | guardian（新規） |

---

## F. [OPEN] 集約（ユーザー裁定必須 / lead 裁量）

### F-1. ユーザー裁定が必須（9 件・推奨案つき）

投資の好み・コスト・運用時間など、ユーザー本人の価値判断が要るもの。

| # | 論点 | 推奨 | 出所 |
|---|---|---|---|
| U-1 | momentum スコア重み（GC0.6/RSI0.4 vs 等加重 vs GC のみ） | 0.6/0.4 | quant §1.2 |
| U-2 | volume_spike 閾値（3.0 倍）・スコアスケール（÷10） | 3.0／÷10 | quant §1.3 |
| U-3 | 無リスク金利 rf（0.0 固定 vs policy/設定化） | 0.0 固定 | quant §2.2 |
| U-4 | P5 ML ラベル（60日超過リターン回帰 vs 20日 vs 2値分類）・回帰/分類 | 60日回帰 | quant §5.2 |
| U-5 | LLM コスト許容上限（夜間毎晩＋チャット往復） | Phase 3 着手時に概算しユーザー裁定 | ai-advisor §5.1/§13 |
| U-6 | 会話履歴の永続先（localStorage 揮発 vs DB 保存） | localStorage（policy 変更は journal に残る） | ai-advisor §6.1・app OPEN-G |
| U-7 | チャットでの policy 更新（即時 vs 承認制） | rationale 即時／構造化コアは proposals 承認制 | ai-advisor §6.2 |
| U-8 | 夜間ドシエの調査頻度・watchlist 件数上限 N | 古い順に 1 晩 N 件・N はユーザー | ai-advisor §11 |
| U-9 | cron 起動時刻（生活時間に合わせる） | 02:00 JST 起点・TZ=Asia/Tokyo | data-arch §6.3 |

> U-5 はコスト、U-1〜U-4 は投資手法の好み、U-6〜U-9 は運用スタイル。いずれも lead が勝手に決めると後で「そうじゃない」になる類。

### F-2. lead 裁量で良い（推奨で確定して進めてよい）

技術的良識で決まり、後から変えても傷が浅いもの。

| # | 論点 | lead 推奨確定値 | 出所 |
|---|---|---|---|
| L-1 | cron 方式（C=APScheduler 同居 vs B=専用サービス） | C で開始・重くなれば B（run_nightly をプロセス非依存に保つ） | data-arch §1.5 |
| L-2 | `/batch/run` 同期 vs 非同期 | 非同期 202＋ロック競合 409 | data-arch §1.6 |
| L-3 | 営業日判定方式 | 曜日で土日除外＋祝日は空レスポンス吸収 | data-arch §1.7 |
| L-4 | is_etf 是正時期 | Phase 7 に温存（Phase 1 は market_code で実質判別可とコメント） | data-arch §1.10 |
| L-5 | 全銘柄マスタ取得 | master 全件取得を実機確認、不可なら daily の code から不足補完 | data-arch §1.11 |
| L-6 | リクエスト間隔 env 化 | `JQUANTS_MIN_INTERVAL_SECONDS`（Free13/Light1） | data-arch §1.9 |
| L-7 | 自分データ FK（張る vs 張らない） | 自分データ（transactions 等）は張る・生データ（daily_quotes→stocks）は既存どおり張らない | data-arch §2.2 |
| L-8 | portfolios 初期行 seed | `(1,'Default')` を `0003` で seed | data-arch §2.3 |
| L-9 | 既定ポートフォリオ解決 | `GET /portfolios` 先頭を既定（id 固定にしない） | app OPEN-C |
| L-10 | IndexAdapter ソース | Stooq 既定・TOPIX/日経は J-Quants 指数 API 確認 | data-arch §2.4 |
| L-11 | 部分取得失敗日に AI を回すか | 回さず通知のみ | data-arch §3.4 |
| L-12 | .pkl シリアライズ | joblib・ファイル名に学習日・`*-latest.json` ポインタ | data-arch §5.3 |
| L-13 | get_indicators 供給方式 | P1 はオンザフライ再計算・常時テーブルは P2 以降 | quant §1.6 |
| L-14 | 期待リターン推定 | historical mean + Ledoit-Wolf | quant §2.3 |
| L-15 | backtest 手数料 | 無視＋注記 | quant §2.4 |
| L-16 | SSE 採否 | Phase 3 は非ストリーミング＋tool_runs 同梱・SSE は後 | ai-advisor §5.5・app OPEN-F |
| L-17 | journal 構造化受け方 | `submit_journal` Tool で受ける（JSON パースより堅い） | ai-advisor §7 |
| L-18 | proposals.depends_on 列 | 追加する（app の依存制御は妥当・低コスト）＝B-8 | app OPEN-E |
| L-19 | Transactions の nav | Portfolio 内タブ | app OPEN-D |
| L-20 | nav「Advisor」 | 専用ページなし・チャット起動トリガ | app OPEN-I |
| L-21 | チャットのリサイズ | 自前 pointer ハンドル（依存増やさない） | app OPEN-H |
| L-22 | watchlist stale しきい値 | 21 日・backend 算出 | app OPEN-J |
| L-23 | investigate 同期/非同期 | 同期で着工・遅ければジョブ化 | app OPEN-K |
| L-24 | ドシエ markdown レンダラ | react-markdown + rehype-sanitize | app OPEN-L |
| L-25 | 通知設定 | Webhook は .env 固定・UI は最小 | app OPEN-M |
| L-26 | adj_close 欠損 | skip（補間しない＝数字を作らない） | quant §0.1 |
| L-27 | watchlist 所属 Phase | Phase 4・ai-advisor 管轄に寄せる（B-13） | guardian |

---

## 付録: lead への申し送り（R3 前にやること）

1. **Alembic 通し番号表を 1 枚作る**（B-1・B-13・全レーンのテーブル追加 Phase を 1 本のチェーンに並べる）。これが無いと R3 で各レーンが番号を直しても再衝突する。
2. **Tool 返却スキーマの正本を 1 枚に固定**（B-2）。`is_delayed`／correlation `{codes,labels,matrix}`／weights 配列／sma 形を決めて quant・ai・app に配る。
3. **単位 0..1 を全レーン横断で宣言**（B-3）。app の % 表記を一掃。
4. **watchlist・financials の所属 Phase/レーンを確定**（B-7・B-13）。
5. **ユーザー裁定 9 件（F-1）を質問リストに**。lead 裁量 27 件（F-2）は推奨で確定して R3 を進めてよい。
