# Phase 3 着工仕様: AI Advisor（2軸・製品の核心）
> 出所: roadmap.md Phase 3 / ADR-011〜016/018/024/025 / advisor.md。レビュー・裁定反映済み。コード未実装＝着工仕様。
> 合成元: `_drafts/_arbitration.md`（正本）・`_drafts/ai-advisor.md`（主担当）・`_drafts/app.md` §P3・`_drafts/data-arch.md` §3・`_drafts/quant.md` §1〜2・`_drafts/_current-state.md` §6・`docs/advisor.md`・`docs/roadmap.md` Phase 3。
> 横断不整合は `_arbitration.md` の裁定（決定1〜8）に機械的に従う。

---

## 0. 目的と完了条件

**目的（roadmap Phase 3）**: 「で、どうすべきか」を AI が一緒に考える。Phase 0〜2 の事実（株価・signals・ポートフォリオ・資産・指数）を材料に、方針づくりと提案を行う。ここが製品の核心。Phase 4 以降は「AI に渡せる材料を増やす」作業。

**1 つの脳・2 つの起動口（ADR-011）**:
- **軸1 夜の分析AI**: Phase 1 で導入済みの cron 夜間バッチに相乗りし、「昨日までの方針（`policy`）」＋「今日の事実」を突き合わせて方針見直しを提案し `advisor_journal` を 1 件生成。方針変更は**承認制**（`proposals.status` で消し込み）。**画面なし**（ADR-025）。
- **軸2 相談チャットAI**: 全ページ常駐チャット（ADR-024）。Tool Calling で Python の計算結果を引き、**根拠付きで銘柄・比率を提案**。画面コンテキスト（軽量ヒント）で指示語を解決。

**不変条件（随所で守る）**:
- **AI は計算しない（ADR-014）**。Python（quant/data）が「事実（数値）」を計算し、LLM は Tool 戻り値を解釈・提案するだけ。プロンプトに生データを丸投げしない。数値は必ず Tool 戻り値に紐づく。
- **CORE（不変・リポジトリ）＋ POLICY（可変・DB）の物理分離（ADR-015）**。チャットは CORE を書き換えない。
- **DB に触れる OS プロセスは FastAPI 1 つだけ（ADR-005）**。夜間バッチは APScheduler で FastAPI プロセス内同居（data-arch 方式C）＝書×書は同一プロセス内で直列化（_arbitration 決定5・B-9）。

**完了条件（roadmap）**: チャットで「資産が小さいので短期はリスク取りたい、でもマイナスは避けたい、ゼロカットは許容」と相談すると、AI がトレードオフを整理して `policy` を更新（承認制）し、夜の分析AI が翌朝それに沿った提案と日記を出す。

---

## 1. 全体像（2軸＝1つの脳・2つの起動口・前提Phase1/2のTool）

```
                     ┌──────────────── 1つの脳 ────────────────┐
                     │  CORE(不変・md)  +  POLICY(可変・DB policy)  │
                     │  +  手法カード(任意)  +  直近 journal(文脈)   │
                     └──────────────────────────────────────────┘
                                    │ build_messages()
              ┌─────────────────────┴─────────────────────┐
       軸1 夜の分析AI（context=None）            軸2 相談チャット（+ ScreenContext）
       cron 相乗り run_nightly_advisor          POST /chat（非ストリーミング）
              │                                        │
              └──────────── Tool ループ（dispatch）─────┘
                          │  registry: schema+handler+min_phase
                          ▼
         quant 純関数 / data 取得関数（実計算＝ADR-014。handler は薄い橋渡し）
   get_indicators(P1) get_signals(P1) screen_stocks(P1)
   get_portfolio_metrics(P2) optimize_portfolio(P2) get_financials(P2) get_asset_overview(P2)
   submit_journal(P3・夜の出力受け) ／ get_dossier・investigate_stock・fetch_news(P4)
              │                                        │
       advisor_journal 1件 + proposal 起票        reply + tool_runs（UI 可視化）
       （承認制・policy 変更は proposals 経由）     policy 更新→journal snapshot（承認制）
```

- **前提（Phase 1/2 の Tool）**: signals/indicators 系は Phase 1（quant）で、metrics/optimize/financials/asset-overview 系は Phase 2 で実装済み。Phase 3 はそれらを **registry の `min_phase` ゲート**で LLM に露出し、handler で薄く呼ぶだけ。
- **LLM アダプタ**: OpenRouter 既定・`.env` 差替（ADR-012）。`complete()` を tools 対応に拡張。
- **障害時**: LLM 失敗→リトライ→ダメなら日記スキップ＋Discord 通知（ADR-018）。

---

## 2. スキーマ変更（`0006_advisor_state`: policy / advisor_journal / proposals(+depends_on)）

**リビジョン**: `0006_advisor_state`（`down_revision=0005_financials`）。**DDL の正本は ai-advisor、移行ファイルの発行（作成）は data-arch が代行**（_arbitration 決定1 の通し番号表）。schema 一元管理のため**同じ `metadata` に追加**（`backend/app/db/schema.py`）。

**[書き手の補注・ADR-002 / _arbitration 決定5・B-9]**: DB に触れる OS プロセスは FastAPI 1 つ（ADR-005）。夜間バッチは APScheduler で FastAPI プロセス内同居（data-arch 方式C）→ `policy`/`proposals` の昼書き込み（チャット/承認）と夜バッチ書き込みは**同一プロセス内で直列化**され、クロスプロセスの書×書競合は原理的に起きない。手動バッチ（`POST /batch/run` 裏ジョブ・`python -m app.scripts.backfill`）は別 OS プロセスになりうるので `data/batch.lock` の `flock` で防御。稀な競合は SQLite `busy_timeout`（例 5000ms）で吸収。→ ADR-002 / data-model.md に補注（DOC-9・data-arch 主管）。**コード追加は busy_timeout 設定のみ**（flock は Phase 1 既出）。

### 2.1 DDL（全列）

```sql
-- policy（1 行運用・版管理機構なし＝ADR-013）
CREATE TABLE policy (
  id                 INTEGER PRIMARY KEY,        -- 1 行運用（id 固定）
  risk_tolerance     TEXT,                       -- "低"/"中"/"高"
  time_horizon       TEXT,                       -- "短"/"中"/"長"
  target_cash_ratio  REAL,                       -- 0..1（UI で ×100）。最適化制約と二重活用
  max_position_weight REAL,                      -- 0..1
  sector_caps        TEXT,                       -- JSON {sector33_code: 0..1}
  target_return      REAL,                       -- 0..1（任意）
  no_leverage        INTEGER,                    -- 0/1（bool）
  exclusions         TEXT,                       -- JSON ["7203", ...] 等
  rationale          TEXT,                       -- 自由文の理念
  updated_at         TEXT                        -- ISO 文字列
);

-- advisor_journal（夜=1件/日 自動・チャットの policy 変更時も当日 journal に snapshot）
CREATE TABLE advisor_journal (
  id                     INTEGER PRIMARY KEY AUTOINCREMENT,
  date                   TEXT NOT NULL,           -- YYYY-MM-DD
  situation_briefing     TEXT,                    -- JSON（その日の事実：signals/portfolio/asset/index。監査用）
  observations           TEXT,                    -- AI 所見（自由文）
  proposal               TEXT,                    -- 当日の提案（自由文 or 参照）
  proposed_policy_change TEXT,                    -- JSON {field, from, to, reason}（任意）
  policy_snapshot        TEXT,                    -- JSON（その時点の policy まるごと・履歴）
  llm_model              TEXT,                    -- 監査用（settings.llm_model）
  created_at             TEXT
);

-- proposals（承認状態・約定はしない＝ADR-001/019）
CREATE TABLE proposals (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  created_date TEXT NOT NULL,                     -- YYYY-MM-DD
  kind         TEXT NOT NULL,                     -- "policy_change"/"buy"/"sell"/"rebalance"
  body         TEXT,                              -- JSON（kind 依存）
  rationale    TEXT,                              -- 根拠（AI の説明）
  status       TEXT NOT NULL DEFAULT 'pending',   -- "pending"/"approved"/"rejected"
  outcome      TEXT,
  resolved_at  TEXT,
  journal_id   INTEGER,                           -- FK→advisor_journal.id（nullable・チャット起票は null 可）
  depends_on   INTEGER,                           -- FK→proposals.id（nullable・承認順制御 policy_change→buy。決定4/B-8）
  FOREIGN KEY (journal_id) REFERENCES advisor_journal(id),
  FOREIGN KEY (depends_on) REFERENCES proposals(id)
);
CREATE INDEX ix_proposals_status ON proposals(status);
```

- 比率系（`target_cash_ratio`/`max_position_weight`/`sector_caps`）は **すべて 0..1**（_arbitration 決定2 の横断ルール）。`optimize_portfolio` の制約にそのまま渡るため DB は 0..1・UI のみ ×100。
- **[L-8/L-9]** `portfolios` 初期行 `(1,'Default')` は data-arch 管轄（Phase 2）。本 Phase は触れない。

### 2.2 状態遷移

```
proposal: pending ──approve──▶ approved ──(kind=policy_change なら)──▶ apply_policy_change → 当日 journal.policy_snapshot 更新
                  └─reject───▶ rejected
          ※ depends_on(別 proposal) が未 approved の間は approve 不可（policy_change→buy の順序を守る・B-8/決定4）。
policy: 単一行を更新（版管理機構なし・ADR-013）。変更のたび当日 journal に snapshot＋理由を残す。
advisor_journal: 夜=1件/日 自動生成。チャットで policy 変更時も当日 journal に snapshot。
proposals.journal_id: 夜の proposal は生成元 journal に紐付け。チャットの proposal は journal_id=null 可。
buy/sell/rebalance の承認は約定を起こさない（ADR-001/019・screens.md §6(c)）。status 上の遷移のみ。
```

---

## 3. CORE/POLICY 分離

### 3.1 CORE プロンプト（`backend/app/advisor/core_prompt.md` 本番版）

ADR-015 の 5 要素を本番（Tool 接続後）の規律に書き換える。**現状の暫定 CORE は「Tool 未接続だから数値を出せない・一般論で論点整理せよ」と自己申告している**（`_current-state.md` §6・`router.py:26` で起動時 1 回読み込み）。これを**「数値は必ず Tool 経由」へ反転**させ、自己申告文を全削除する。

```markdown
# 役割（要素①）
あなたは AssetVane の規律あるクオンツ投資アナリスト。一般論やニュースの寄せ集めで語らず、
与えられた定量データ（Tool の戻り値）のみを根拠に判断する。日本語で応答する。

# 方法論（要素②・"スキル"）
- モメンタム判定は移動平均・出来高・RSI を必ず併用し、単一指標で結論しない。
- ポートフォリオ提案では相関・シャープレシオ・最大ドローダウンを必ず確認する。
- 割安判断は PER 単体を禁止し、成長率と併せて見る。
- スクリーニング結果は順位そのままでなく、policy 制約（除外・業種上限）で必ず濾す。

# 規律・ガードレール（要素③）
- 数値は Tool の戻り値のみを使う。記憶・推測で数字を作らない（捏造の絶対禁止）。
- 定量的主張をする前に、必ず対応する Tool を呼ぶ。Tool を呼ばずに数値を述べない。
- 不確実なことは「不確実」と明示する。断定を避ける。トレードオフを必ず提示する。
- 株価・評価額は Free プランで約 12 週間遅延しうる。遅延データに基づく判断はその旨を添える。
- AssetVane は発注しない。買い/売りは「提案」であり、実行はユーザーが手動で行う（ADR-001）。

# Tool の使い方（要素④）
- 利用可能な Tool は実行時に提示される。事実が必要なら必ず Tool を呼ぶ。
- 画面コンテキスト（「銘柄 6920 の詳細を見ている」等）はヒントにすぎない。
  数値が要るなら指示語を解決した上で該当 Tool（get_indicators(6920) 等）で取り直す。

# 出力の型（要素⑤）
- 結論には必ず「どのデータ・どの手法から導いたか」の根拠と、想定リスクを添える。
- 提案（銘柄・比率・方針変更）は、根拠・リスク・policy とのトレードオフをセットで示す。
```

- **変更方法**: jj 版管理・意図的コミットのみ。チャット AI は触れない（ADR-015）。起動時 1 回読み込みで運用継続。
- **[突合 quant]** 方法論②の手法名（モメンタム・RSI・相関・シャープ・最大DD）は quant の実装手法・指標名と一致させる。

### 3.2 POLICY コンパイラ（`backend/app/advisor/policy_compiler.py`・新規）

DB `policy` 1 行 dict を POLICY 層の自然文へ整形（ADR-013/advisor.md §3）。**数値・列をそのまま載せず、判断の制約／志向として文章化**（生データ丸投げ禁止＝ADR-014 の精神）。

```python
def compile_policy(policy: dict[str, object] | None) -> str:
    """policy 行を POLICY 層の自然文に整形する（advisor.md §3）。
    policy が None（未設定）なら「方針はまだ設定されていない。対話で引き出す」旨の
    1 段落を返す（チャット初回でも壊れない）。"""
```

整形ルール（構造化コア → 文）:
- `risk_tolerance` / `time_horizon` → 「リスク許容度は高め・短〜中期」
- `target_cash_ratio` / `max_position_weight` / `sector_caps` → 「現金バッファ X% を尊重」「1 銘柄上限 Y%」「業種上限は…」（UI 同様 ×100 して % で文章化）
- `target_return` → 「目標リターンは高め」
- `no_leverage`(true) → 「信用・レバレッジは使わない。個別の全損（ゼロカット）は受容するが借金は負わない」
- `exclusions` → 「次は除外: …」／`rationale` → 末尾に理念として差す

- **二重活用（ADR-013）**: 同じ構造化コアは **quant の `optimize_portfolio` の制約**にも渡る。POLICY 文と最適化制約は**同じ policy 行から**作る（真実は 1 つ）。本書は「policy → 文」、quant は「policy → 制約」。policy のキー名・単位（0..1）を共有（§2 の列定義が正本）。

---

## 4. Tool Calling

### 4.1 レジストリ（`backend/app/advisor/tools/registry.py`・新規）— 単一の真実

各 Tool を「OpenAI tools スキーマ＋handler 関数＋min_phase」で宣言。スキーマと実装がズレない単一の真実。

```python
@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str                          # LLM 向け説明（いつ呼ぶか）
    parameters: dict[str, object]             # JSON Schema（引数）
    handler: Callable[..., Awaitable[dict]]   # 実体（handlers.py）。返却は素の dict（JSON 化）
    min_phase: int                            # 投入フェーズ（1/2/3/4…）

REGISTRY: dict[str, ToolDef] = { ... }

def openai_tools(available_phase: int) -> list[dict]:
    """min_phase <= available_phase の Tool だけ OpenAI tools 配列にして返す。"""
```

- **Phase ゲート**: `min_phase` で「まだ実装されていない Tool を LLM に見せない」。Phase 3 時点で indicators/signals/screen（P1）・metrics/optimize/financials/asset-overview（P2）・submit_journal（P3）を露出、dossier 系（P4）は非露出。

### 4.2 dispatch ループ（`backend/app/advisor/service.py` or `router.py`）

```
1. build_messages(...) で初期メッセージ列を作る
2. resp = await complete(messages, tools=openai_tools(current_phase))
3. while resp.tool_calls:
     for tc in resp.tool_calls:
         result = await REGISTRY[tc.name].handler(tc.arguments)   # 未知 name は {error} を返す（落とさない）
         messages.append(assistant tool_call 記録)                 # OpenAI 形式
         messages.append({role:"tool", tool_call_id: tc.id, content: json.dumps(result)})
     resp = await complete(messages, tools=...)
     # 安全弁: ループ上限（例 max 6 往復）。超過したら打ち切り最終応答を促す
4. return resp.content, tool_runs   # 最終テキスト ＋ 呼んだ Tool 名/引数（tool_runs）
```

- **[正本・_arbitration 決定3・L-16]** Phase 3 は**非ストリーミング**。`ChatResponse` に **`tool_runs: [{name, args?}]`**（app 正本・`tool_calls_made: string[]` は廃止）を足して UI に出す。**結果の数値は載せない**（ADR-025）。SSE は Phase 3 後に検討。

### 4.3 LLM アダプタの tools 拡張（`backend/app/advisor/llm.py`・変更）

```python
class ToolCall(BaseModel):
    id: str                              # OpenAI 互換の tool_call_id
    name: str                            # Tool 名（registry のキー）
    arguments: dict[str, object]         # パース済み引数（json.loads 済）

class LLMResponse(BaseModel):
    content: str | None                  # テキスト応答（tool_calls 時は None のことがある）
    tool_calls: list[ToolCall]           # LLM が要求した Tool 呼び出し（空なら最終応答）

async def complete(
    messages: list[dict[str, object]],
    *,
    tools: list[dict[str, object]] | None = None,   # OpenAI tools スキーマ（registry が供給）
    stream: bool = False,                            # 将来用（Phase 3 は False 固定）
) -> LLMResponse:
    """messages（＋tools）を LLM に投げ、テキストか tool_calls を返す。計算はしない（ADR-014）。"""
```

- 既存 `/chat` の戻り `{reply}` 契約は壊さない（router 側が最終 `content` を `reply` に詰める）。
- **既定モデル（確定）**: 現 `anthropic/claude-sonnet-4-6`（config.py:31）を維持（Tool Calling 品質帯・ADR-012）。
- **インフラ強化（data-arch §3.3）**: `complete()` にタイムアウト＋リトライ。`config.py` に `llm_timeout_seconds: float = 60.0` / `llm_max_retries: int = 3` / `llm_retry_base_seconds: float = 2.0` を追記。`AsyncOpenAI` の `max_retries`/`timeout` を使う。

### 4.4 引数/返却スキーマ（`backend/app/advisor/tools/schemas.py`・新規）— **正本に完全一致**

**[正本・_arbitration 決定2]** quant の純関数が「事実」を計算 → handler が薄く包む → app の REST 型と一致。**全レーン共通の約束**:
- 比率・weight・cash_ratio・deviation の current/limit は **すべて 0..1**（DB/API/Tool）。UI でのみ ×100。
- 遅延フラグは **`is_delayed: bool`**（`delayed` は廃止）。鮮度日は **`as_of: "YYYY-MM-DD"`**。
- correlation は **`{codes: string[], labels: string[], matrix: number[][]}`**（順序保証）。
- weights は **配列 `[{code, current_weight, target_weight, delta}]`**（dict 返し禁止・順序安定）。

```jsonc
get_indicators(code) ->
  {code, as_of, adj_close, sma25, sma75, rsi14, vol_ma20, is_delayed}
  // 平坦。sma5 は P1 では計算しない。ネスト sma:{} は採用しない。

get_signals(date?, type?, code?) ->
  {date, is_delayed, signals: [{code, company_name, signal_type, score, payload}]}
  // company_name は signals JOIN stocks（ルータ）。行レベル date は持たない（トップのみ）。
  // payload(JSON) に label(短文) と change_5d を quant が格納（B-6）。

screen_stocks(criteria) ->
  {date, is_delayed, items: [{code, company_name, signal_type, score, indicators}]}
  // criteria キーは内部列名: {signal_type?, sector33_code?, min_score?, limit?}（min_score は 0..1）。
  // 各 item は payload ではなく indicators（平坦な指標値 dict）。

get_portfolio_metrics(portfolio_id) ->
  {portfolio_id, as_of, annual_return, annual_volatility, sharpe, max_drawdown,
   correlation: {codes, labels, matrix}, lookback_days, is_delayed,
   deviations: [{kind, label, current, limit, breached}]}
  // current/limit は 0..1。deviations は quant の単一関数（B-12）。

optimize_portfolio(portfolio_id) ->
  {portfolio_id, as_of, objective, cash_weight,
   weights: [{code, current_weight, target_weight, delta}],
   expected_annual_return, expected_annual_volatility, expected_sharpe,
   constraints_applied, infeasible}
  // weights/cash_weight は 0..1。policy 制約適用は quant。

get_financials(code) ->
  {code, items: [{disclosed_date, fiscal_period, net_sales, operating_profit, profit, eps, bps}]}
  // financials テーブル(0005)から。data-arch が取得仕様を定義。

get_asset_overview() ->
  {as_of, total_value, stock_value, cash_value, external_value, pnl, is_delayed,
   allocation: [{name, value, weight}], policy_targets,
   deviations: [{kind, label, current, limit, breached}], trend}
  // allocation は name 単位(株式/現金/投信)・weight は 0..1。
  // deviations は get_portfolio_metrics と同じ Python 関数(quant)から供給（出力先2・計算1）。

submit_journal(observations, proposal?, proposed_policy_change?) ->
  {ok: true}   // 軸1 夜の出力受け（§5）。proposed_policy_change は {field, from, to, reason}。min_phase=3。

// --- Phase 4（min_phase=4・本 Phase では非露出）---
get_dossier(code) -> {code, summary_md, key_facts, last_investigated_at, sources:[{url,title,summary,published_at,source_type}]}
investigate_stock(code) -> {code, summary_md, key_facts, last_investigated_at, n_sources_added}
fetch_news(code, since?) -> {code, articles:[{url,title,summary,published_at,source_type}]}
```

- 全 Tool の返却は**素の dict**（registry が `json.dumps` して `tool` ロールへ）。
- **[B-12]** policy 逸脱（`deviations`）は **quant の単一関数 `compute_deviations()`** が計算し、`get_portfolio_metrics`（Tool）と `get_asset_overview`（画面）の**両方に同値**を供給（計算 1 か所・出力先 2）。AI には計算させない（ADR-014）。

### 4.5 handler（`backend/app/advisor/tools/handlers.py`・新規）— 薄い橋渡し（ADR-014）

各 handler は「LLM 引数（dict）→ schemas.py で検証 → quant/data の実関数呼び出し → 返却 dict」の**薄い橋渡しのみ**。ロジック・計算は持たない（ADR-014・レイヤ分離）。

```python
async def handle_get_indicators(args: dict) -> dict:
    code = GetIndicatorsArgs(**args).code        # schemas.py で検証
    return quant.compute_indicators(code)        # quant の実関数（戻りはそのまま）
```

- **[突合 quant/data]** `quant.compute_indicators`（P1 オンザフライ再計算＝L-13）・`quant.compute_portfolio_metrics`・`quant.optimize_portfolio`・`data.get_financials` 等の**実関数名・所在は quant/data が決める**。本書は「handler が呼ぶ」契約だけ固定。例外は handler 内で握り `{error: "…"}` を返す（ループを落とさない）。

### 4.6 手法カード（初期は省略可・ADR-016）

- 初期はベタ書き list で十分。`method_cards` テーブル・embedding は作らない（ADR-016）。Phase 3 は `method_cards=None` で開始してよい。手法が増えたら `prompt_builder` の `method_cards` に全列挙 → 将来 RAG（`sqlite-vec`）。**計算は持たない**（索引＋参照知識のみ）。

---

## 5. 軸1 夜の分析AI（`backend/app/advisor/nightly.py`・新規）

cron 夜間バッチ（Phase 1 で導入・data-arch）に相乗り。**画面 context 無し**（ADR-025）。data-arch の `backend/app/batch/jobs/run_advisor.py` が `NIGHTLY_JOBS` の**末尾**（signals 計算後＝事実が揃ってから）に追加し、本書の `run_nightly_advisor()` を呼ぶ。

```python
async def run_nightly_advisor(conn: Connection) -> None:
    """その日の事実を集め、advisor_journal を 1 件生成し proposal を起票する（ADR-011/018）。

    1. policy = repo.get_policy(conn)
    2. briefing = collect_situation_briefing(conn)     # 今日の signals/portfolio/資産/指数（dict・quant/data 供給）
    3. messages = build_messages(core_prompt=_CORE, policy=policy,
           conversation=[user: 夜の定型指示],          # 「昨日までの方針と今日の事実を突き合わせ、見直しを提案せよ」
           screen_context=None,                        # 軸1 は画面が無い（ADR-025）
           recent_journal=repo.get_recent_journal_summary(conn))
    4. reply, tool_runs = await run_tool_loop(messages, phase=current_phase())
       # 夜も同じ Tool を使える。get_signals/get_portfolio_metrics 等で事実を取り直す。
       # 最終ターンで LLM が submit_journal(observations, proposal?, proposed_policy_change?) を呼ぶ。
    5. journal_id = repo.insert_journal(conn, date, situation_briefing=briefing(JSON),
           observations=…, proposal=…, proposed_policy_change=…(JSON),
           policy_snapshot=policy(JSON), llm_model=settings.llm_model)
    6. if proposed_policy_change:
           repo.insert_proposal(conn, kind="policy_change", body=…, rationale=…,
               status="pending", journal_id=journal_id)
    7. 失敗時（LLM タイムアウト等）はリトライ→ダメなら日記スキップして記録し Discord 通知（§7）
    """
```

- **collect_situation_briefing**: Tool と同じ事実取得関数を呼んで dict に集約（quant/data 供給）。`advisor_journal.situation_briefing` に JSON 保存（監査・「何を見て判断したか」を後から辿る）。
- **[確定・L-17/決定7]** 夜の出力は**専用 Tool `submit_journal(observations, proposal, proposed_policy_change?)` で受ける**（JSON 文字列パースより堅い・ADR-014 整合）。`proposed_policy_change` の形は `{field, from, to, reason}`。registry に `min_phase=3` 登録。
- **データ取得が部分失敗のとき（data-arch §3.4・推奨）**: 当日の `fetch_quotes` が ok なら回す、失敗なら回さず通知のみ（古い材料で提案させない）。

---

## 6. 軸2 相談チャットAI（`backend/app/advisor/router.py`・変更）

現状（CORE 差し込みのみ・ステートレス・Tool 未接続）を、Tool ループ＋context 注入へ。

### 6.1 プロンプト組み立て（`backend/app/advisor/prompt_builder.py`・新規）

advisor.md §6 の層を 1 関数に集約。**軸1・軸2 共通**（違うのは context の有無と起動契機だけ＝ADR-011）。

```python
def build_messages(
    *,
    core_prompt: str,                              # core_prompt.md（不変）
    policy: dict[str, object] | None,               # DB policy 1 行
    conversation: list[Message],                    # 軸2: 会話履歴 / 軸1: [user=夜の指示文] 1 件
    screen_context: ScreenContext | None = None,    # 軸2 のみ。軸1 は必ず None（ADR-025）
    method_cards: list[str] | None = None,          # 該当時のみ（初期は None・§4.6）
    recent_journal: str | None = None,              # 直近の投資日記の要約（文脈・連続性）
    facts: None = None,                             # 事実は Tool ループで動的に入る（ここでは積まない）
) -> list[dict[str, object]]:
    """advisor.md §6 の順序でメッセージ列を組む。"""
```

**組み立て順序（system → 会話）**:
```
system: [CORE]                ← core_prompt.md
system: [POLICY]              ← compile_policy(policy)
system: [手法カード]           ← method_cards があれば（初期は省略可）
system: [文脈]                ← recent_journal があれば「直近の投資日記: …」
system: [画面コンテキスト]      ← 軸2 のみ。compile_screen_context() の 1 行
...conversation（user/assistant の列）
```
- **[事実]は messages に静的に積まない**。Tool ループ（§4.2）で `tool` ロールとして動的挿入＝ADR-014 を構造的に担保。
- system を複数に分けるか 1 本連結かは実装裁量。CORE と POLICY の物理分離（ADR-015）はファイル/DB レベルで担保済み。

### 6.2 ScreenContext 注入（軸2・ADR-025）— **focus は正本形**

```python
class FocusRef(BaseModel):
    type: Literal["stock", "portfolio", "signal", "proposal"]
    code: str | None = None          # stock / signal
    id: int | None = None            # portfolio / proposal

class ScreenContext(BaseModel):
    page: str                        # "stock_detail" / "dashboard" / "signals" / "policy" / ...
    focus: FocusRef | None = None    # 対象が無いページは省略

def compile_screen_context(ctx: ScreenContext) -> str:
    """画面 context を 1 行の自然文へ（数値は載せない）。
    例:「ユーザーは銘柄 6920 の詳細ページを見ている」"""
```
- **[正本・_arbitration 決定3・B-4]** `focus` は **`{type: "stock"|"portfolio"|"signal"|"proposal", code?: string, id?: number}`**。**type で使い分け**: `stock`/`signal` は `code`、`portfolio`/`proposal` は `id`。
- **数値・画面データは絶対に載せない**（ADR-025）。「何の話か」のヒントのみ。AI は数値が要れば該当 Tool で取り直す。揮発情報で DB 保存しない（送信時のみ使用）。

### 6.3 router（`/chat` 改修）

```python
class ChatRequest(BaseModel):
    messages: list[Message]                       # 既存（user/assistant のみ・system 不可）
    context: ScreenContext | None = None          # ADR-025（§6.2）

class ToolRun(BaseModel):
    name: str
    args: dict[str, object] | None = None         # 結果の数値は載せない（ADR-025）

class ChatResponse(BaseModel):
    reply: str
    tool_runs: list[ToolRun] = []                 # UI 可視化用（§4.2・正本 = app 形）

@router.post("/chat")
async def chat(req: ChatRequest) -> ChatResponse:
    policy = repo.get_policy(conn)
    recent = repo.get_recent_journal_summary(conn)
    messages = build_messages(core_prompt=_CORE, policy=policy,
                              conversation=req.messages, screen_context=req.context,
                              recent_journal=recent)
    reply, tool_runs = await run_tool_loop(messages, phase=current_phase())
    return ChatResponse(reply=reply, tool_runs=tool_runs)
```

### 6.4 会話履歴の扱い（現状ステートレスの是正方針）

- **方針**: Phase 3 は**ステートレス維持を既定**（frontend が会話を保持・毎ターン全 messages 送信）。サーバは会話を DB 保存しない。ADR-024 が会話保持を frontend に置く前提。会話の DB 永続は単一ユーザーに過剰で、`advisor_journal`（方針の履歴）とは別物。
- **[U-6・ユーザー裁定]** 永続先: **既定 localStorage（リロードで消えてよい）**。`policy` の変更は journal に必ず snapshot されるので重要な決定は失われない（`_open-questions.md`）。
- **トークン肥大対策**: Phase 3 は素朴に全送信、肥大が問題化したら server 側で古い turn を要約（後付け可）。

### 6.5 チャットでの policy 更新 → journal snapshot

- 方針が変わる場合、AI は `policy` を直接 Tool で書き換えない（チャットが方針を勝手に書き換えない規律）。代わりに**提案として `proposals`（kind=`policy_change`）を起票**し、ユーザー承認後に `policy` 更新＋journal snapshot（§5/service）。
- **[U-7・ユーザー裁定]** 既定: **構造化コア（上限・除外・no_leverage）の変更は proposals 承認制**、**軽微な `rationale` 追記はチャット即時更新を許す**（ADR-013「気軽に育てる」と ADR-018 承認制の折衷・`_open-questions.md`）。

---

## 7. LLM アダプタ・障害処理（ADR-012/018）

- **インフラ（ADR-012）**: `.env` の `LLM_API_KEY`/`LLM_BASE_URL`/`LLM_MODEL` で OpenRouter（既定）↔ Ollama 差替（`config.py:28-31` 既設）。本 Phase で `llm_timeout_seconds=60.0`/`llm_max_retries=3`/`llm_retry_base_seconds=2.0` を `config.py` に追記（data-arch §3.3）。
- **リトライ**: LLM 呼び出しは指数バックオフ・上限あり（`AsyncOpenAI` の `max_retries`/`timeout`）。
- **失敗時（ADR-018）**: ダメなら**その日の journal をスキップ**して「失敗を記録」し、`DISCORD_WEBHOOK_URL` へエラー通知。signals は前日分が残る（夜の分析失敗が data 取得を壊さない）。
- **[確定]** Discord 通知ユーティリティ（DiscordAdapter / `backend/app/batch/notify.py`・data-arch §1.12/§6.1 管轄）を使い、**夜の分析失敗のエラー通知だけ Phase 3 で最小実装**（本格通知は Phase 6）。`DISCORD_WEBHOOK_URL` 未設定なら no-op（ログのみ）。

---

## 8. REST API 契約（`backend/app/routers/advisor_state.py`・新規／app レーンと契約共有）

api.md §4 準拠。**[突合 app]** HTTP 入出力は app レーン #4、本書のサーバ側ロジック（`backend/app/advisor/service.py`）が実体。ルータの所在（`routers/` か `advisor/`）は app に合わせる。

### 8.1 service.py（状態遷移の共通ロジック）

```python
def apply_policy_change(conn, *, change: dict, source: Literal["chat","nightly"],
                        journal_id: int | None) -> None:
    """policy を更新し、その日の advisor_journal に policy_snapshot を残す（ADR-013）。"""

def resolve_proposal(conn, proposal_id: int, *, decision: Literal["approved","rejected"],
                     outcome: str | None = None) -> None:
    """proposals.status を遷移。approved かつ kind=policy_change なら apply_policy_change を呼ぶ。
    depends_on が指す提案が未 approved の間は approve を弾く（承認順制御・B-8/決定4）。"""
```

### 8.2 エンドポイント（Pydantic／TS 型は app §P3 正本）

- **`GET /policy`** → `Policy {core: PolicyCore, rationale, updated_at}`（**core と rationale を分けて返す**・screens.md §3）。`PolicyCore = {risk_tolerance, time_horizon, target_cash_ratio(0..1), max_position_weight(0..1), sector_caps(0..1), target_return, no_leverage(bool), exclusions[]}`。`no_leverage` の int↔bool・`sector_caps`/`exclusions` の JSON↔型変換はルータ層。
- **`PUT /policy`** body `PolicyUpdate {core?: Partial<PolicyCore>, rationale?}` → `upsert_policy`＋journal snapshot。チャット経由更新（§6.5）と Policy 画面直接編集の**両方の入口**が同じ `PUT /policy` を叩く。
- **`GET /journal?from=&to=`** → `JournalResponse {entries: JournalEntry[]}`（date 降順）。`JournalEntry = {id, date, observations, proposal, proposed_policy_change, policy_snapshot, llm_model, created_at}`。`situation_briefing`（重い JSON）は**一覧では返さず、必要なら別途 `GET /journal/{id}`**。
- **`GET /proposals?status=`** → `ProposalsResponse {proposals: Proposal[]}`。`Proposal = {id, created_date, kind, body, rationale, status, outcome, resolved_at, journal_id, depends_on}`。`depends_on`(FK→proposals.id・決定4)。`body` の kind 別中身（policy_change/buy/sell/rebalance）は ai-advisor 確定（app は入れ物 `ProposalBody` を規定）。
- **`POST /proposals/{id}/approve`** body `{outcome?}` → `ResolveResult {proposal}`（status=approved・`resolve_proposal`）。
- **`POST /proposals/{id}/reject`** body `{outcome?}` → `ResolveResult {proposal}`（status=rejected）。
- **`POST /chat`** → §6.3（`ChatRequest{messages, context?}` → `ChatResponse{reply, tool_runs[]}`・非ストリーミング）。

### 8.3 repo 関数（`backend/app/db/repo.py`・変更／素の dict 返し）

```python
get_policy(conn) -> dict | None
upsert_policy(conn, fields: dict) -> None             # 1 行運用（id 固定）
insert_journal(conn, **fields) -> int                  # journal_id を返す
get_recent_journal_summary(conn, n: int = 1) -> str | None
list_journal(conn, from_=None, to=None) -> list[dict]
insert_proposal(conn, **fields) -> int
list_proposals(conn, status: str | None = None) -> list[dict]
get_proposal(conn, id: int) -> dict | None
update_proposal_status(conn, id, status, outcome=None, resolved_at=…) -> None
```

---

## 9. frontend（パス・常駐チャット・承認UI・policy 編集UI）

設計の真実は app §P3-4/5/6/7。スタイルは DESIGN.md トークン（density-first）。

### 9.1 常駐フローティングチャット（`frontend/src/components/advisor/AdvisorChat.tsx`・変更）
- **root layout 配置のまま**（ADR-024・ページ遷移で会話保持）。既存はドラッグ・最小化あり。
- **画面コンテキスト送信**（ADR-025）: 現状ハードコードの「見ているページ: Dashboard」を `usePathname()` ＋ route→page マップ（app 付録 B）で実値化し、`ChatRequest.context` に載せる。`/stocks/[code]` → `{type:"stock", code}` 等。**数値は載せない**。
- **tool_runs 可視化**（screens.md §4）: `ChatResponse.tool_runs` を assistant バブル上部にチップ表示（「⚙ get_signals 実行」）。結果値は出さない。
- **会話の永続**: `localStorage`（U-6・サーバはステートレス維持）。DB 永続は持たない。
- **ドラッグ/リサイズ/最小化**: リサイズ未実装 → **自前 pointer ハンドル**（依存を増やさない・OPEN-H 確定）。
- nav「Advisor」は専用ページを作らず**チャット起動トリガ**（onClick で open・OPEN-I 確定）。

### 9.2 policy 編集UI（`frontend/src/app/policy/page.tsx`・`components/policy/PolicyEditor.tsx`・新規）
- 構造化コア（チップ/グリッド・編集可）＋ rationale（テキストエリア）。保存は `putPolicy`。「チャットで調整」導線も併置。`target_cash_ratio` 等は **0..1 を ×100 して % 表示**、保存時 ÷100。

### 9.3 提案承認UI（`frontend/src/app/proposals/page.tsx`・`components/proposals/ProposalCard.tsx`・新規）
- status タブ（pending/approved/rejected）＋承認/却下。kind バッジ（POLICY=accent / BUY=up / SELL=down）。**`depends_on` が未承認なら承認ボタン無効＋注記**（承認順制御）。`approveProposal`/`rejectProposal` 接続。

### 9.4 journal（`frontend/src/app/journal/page.tsx`・新規）
- 日記一覧（`getJournal`）。各エントリに observations 本文＋`policy_snapshot` の差分チップ。

### 9.5 lib/api.ts（`frontend/src/lib/api.ts`・変更）
- 型 `Policy`/`PolicyCore`/`PolicyUpdate`/`JournalEntry`/`Proposal`/`ChatRequest`/`ChatContext`/`FocusRef`/`ChatResponse`/`ToolRun` を追加（backend Pydantic と 1:1）。
- 関数 `getPolicy`/`putPolicy`/`getJournal`/`getProposals`/`approveProposal`/`rejectProposal`/`sendChat`（チャットを api.ts に集約・現状 AdvisorChat 内の直 fetch を移設）。`sendChatStream` は SSE 用に口だけ予約（Phase 3 未実装）。

### 9.6 Dashboard 実配線・nav
- `app/page.tsx` の `policy`/`proposals`/`journal` を `getPolicy()`/`getProposals("pending")`/`getJournal()` に差し替え（`mock-data.ts` の該当 mock 削除）。
- Sidebar nav の `Policy`/`Journal`/`Proposals` を href 化。

---

## 10. テスト計画

DB は既存方針どおり**一時 SQLite**（conftest）。LLM/外部は必ずモック（ネットを叩かない）。

- **policy_compiler**: policy dict（全部入り／None／部分欠損）→ 期待文に主要トークンが含まれるか（contains で脆くしない）。
- **prompt_builder**: 層の順序（CORE→POLICY→手法→文脈→context→会話）と、軸1 で `screen_context=None` が強制されること。事実が静的に積まれないこと。
- **Tool registry/dispatch**: `openai_tools(phase)` の Phase ゲート（min_phase 超は出さない）。未知 Tool 名で `{error}` を返しループが落ちないこと。handler は quant/data 関数を**モック**して橋渡しのみ検証。
- **Tool ループ**: tool_calls あり→handler 実行→tool ロール挿入→再 complete、の往復と**ループ上限**で打ち切ること（LLM はモック）。`tool_runs` に呼んだ Tool が記録され**結果値は載らない**こと。
- **状態遷移（service）**: pending→approved（policy_change なら policy 更新＋journal snapshot）／→rejected。`depends_on` 未承認なら approve が弾かれること。buy 承認が約定を起こさないこと。
- **nightly**: LLM 失敗時に journal スキップ＋Discord 通知が呼ばれること（LLM/Discord はモック）。`submit_journal` 経由で observations/proposal が記録され、`proposed_policy_change` があれば proposal が起票されること。
- **REST**: `GET /policy` が core/rationale 分離・`no_leverage` int↔bool・`sector_caps`/`exclusions` JSON↔型変換が効くこと。`PUT /policy` 部分更新。

---

## 11. 着工順（チェックリスト）

着工順（ai-advisor §0）: **A → B → C → D → F → E**（昼チャットを先に通す）。data-arch 側は ①LLM リトライ/タイムアウト → ②`0006` 移行発行 → ③`run_advisor` 配線 → ④失敗時テスト。

- [ ] **A. CORE 本番版**（`core_prompt.md` を「数値は必ず Tool 経由」へ全面改稿・暫定自己申告を削除）＋ **POLICY コンパイラ**（`policy_compiler.py`）。
- [ ] **schema/移行**: `0006_advisor_state`（policy/advisor_journal/proposals+depends_on）を `schema.py` に追加・移行ファイル発行（data-arch 代行）。`busy_timeout` 設定。repo に get/upsert/list/状態遷移クエリ。
- [ ] **B. Tool レイヤ**: `tools/registry.py`（ToolDef+min_phase+openai_tools）・`tools/schemas.py`（正本スキーマ）・`tools/handlers.py`（quant/data への薄い橋渡し）・`llm.py` の tools/retry 拡張・`config.py` に LLM タイムアウト/リトライ追記。
- [ ] **C. プロンプト組み立て**: `prompt_builder.py`（build_messages＋compile_screen_context・ScreenContext/FocusRef）。
- [ ] **D. 軸2 チャット**: `router.py` を Tool ループ化・`context` 受け取り・`ChatResponse{reply, tool_runs}`。Tool 1 個（例 get_signals）から通す。
- [ ] **F. 状態遷移・承認 API**: `service.py`（apply_policy_change/resolve_proposal・depends_on 制御）＋ `routers/advisor_state.py`（/policy・/journal・/proposals・approve/reject）。
- [ ] **E. 軸1 夜AI**: `nightly.py`（run_nightly_advisor・collect_situation_briefing・submit_journal Tool・proposal 起票）。data-arch の `batch/jobs/run_advisor.py` から NIGHTLY_JOBS 末尾に配線。失敗時スキップ＋Discord 通知。
- [ ] **frontend**: lib/api.ts 型/関数 → AdvisorChat 改修（context/tool_runs/localStorage/リサイズ）→ Policy/Proposals/Journal 画面 → Dashboard 実配線・nav href 化。
- [ ] **テスト**（§10）一式。

---

## 12. このPhaseの[OPEN]（`_open-questions.md` 参照・推奨値を既定として実装）

| # | 論点 | 既定（実装する値） | 差し替え手段 |
|---|---|---|---|
| **U-5** | LLM のコスト許容上限（夜間毎晩＋チャット往復のトークン量） | **Phase 3 着手時に概算してユーザー確認**。既定モデルは `anthropic/claude-sonnet-4-6` 維持 | `.env` でモデル差替（ADR-012・易） |
| **U-6** | 会話履歴の永続先 | **localStorage（揮発・リロードで消えてよい）**。policy 変更は journal に snapshot されるので重要な決定は失われない | DB 保存層追加（中） |
| **U-7** | チャットでの policy 更新 | **構造化コア（上限・除外・no_leverage）の変更は proposals 承認制**／`rationale` 追記はチャット即時反映 | 設定で全即時/全承認制に切替（易） |

- 上記は投資の好み・コスト・運用に関わるため `_open-questions.md` でユーザー確認に回す。**値は env/policy/設定で後から差し替え可能な形**で実装する前提。
- 確定済み（参考）: SSE=Phase 3 非ストリーミング＋`tool_runs` 同梱（L-16）／journal 構造化=`submit_journal` Tool（L-17）／focus 型=`{type, code?, id?}`（決定3）／Discord エラー通知のみ P3 最小（ADR-018）。

---

## 付録. [DOCS要修正]（lead 統合の対象・正本に反映済み）
- **DOC-4 / api.md §4** `/chat` body に `context` 明記＋`focus: {type, code?, id?}` に拡張（決定3）。本書 §6.2 と一致。
- **DOC-9 / ADR-002 or data-model.md** 「書き手の系統は FastAPI 1 プロセス内同居で直列化＋busy_timeout」を補注（決定5・data-arch 主管）。本書 §2 と一致。
- **DOC-12 / roadmap・data-model.md** `watchlist` の所属を Phase 4（ai-advisor `0007_dossier`）に確定（決定1・B-13）。本書では Phase 4 として扱い、本 Phase（`0006`）では作らない。
