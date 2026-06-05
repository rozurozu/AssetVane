# AI Advisor 設計（Phase 3 / Phase 4）— 着工可能仕様ドラフト

> 担当レーン: **ai-advisor**（タスク #5）。コードは書かない設計仕様。
> 参照: [advisor.md](../../advisor.md)（主担当・精読済）／[decisions.md](../../decisions.md) ADR-011/012/013/014/015/016/018/020/024/025／[data-model.md](../../data-model.md)（policy/advisor_journal/proposals/stock_dossiers/dossier_sources/method_cards）／[api.md §4-5](../../api.md)／[screens.md §4-5](../../screens.md)／[roadmap.md Phase 3-4](../../roadmap.md)。
> 現状接地: [_current-state.md](_current-state.md) §6（既存 `/chat` は **CORE 差し込みのみ・Tool 完全未接続・ステートレス・画面 context 未配線**）。
>
> **他レーンとの突き合わせ（最重要）**: 本書の Tool 契約（関数名・引数・返却 JSON）は
> - **quant レーン（#3）** … `get_indicators` / `get_signals` / `screen_stocks` / `get_portfolio_metrics` / `optimize_portfolio` の**数値を実計算で供給**。関数名・返却キーを一致させること。
> - **data レーン（#2）** … `get_financials`（`financials` テーブル）・`investigate_stock`/`fetch_news` のデータ取得・`batch/`・cron。
> - **app レーン（#4）** … `/chat` 契約（`context` フィールド）・Tool 実行の UI 可視化・`/policy`/`/journal`/`/proposals`/`/dossiers` の REST 契約。
>
> 突き合わせポイントは各節末の **[突合]** で明示する。

---

## 0. このレーンが決め切る範囲と着工順（サマリ）

| # | 成果物 | Phase | 依存 |
|---|---|---|---|
| A | CORE プロンプトの本番版（`core_prompt.md` 書き換え）＋ POLICY コンパイラ | 3 | `0006_advisor_state`（policy・本レーン定義正本） |
| B | Tool レイヤ（レジストリ・dispatch・LLM アダプタの tools 対応・Tool 実装の薄いラッパ） | 3 | quant/data の実計算関数（返却=_arbitration 決定2） |
| C | プロンプト組み立て器（CORE+POLICY+事実+文脈+画面 context） | 3 | A・B |
| D | 軸2 相談チャット（`/chat` の Tool ループ化・画面 context 注入・policy 更新→journal snapshot） | 3 | C・app の `/chat` 契約（決定3） |
| E | 軸1 夜の分析 AI（`run_nightly_advisor()`・cron 相乗り・journal 生成・proposal 起票） | 3 | C・data の batch/cron（方式C 同居） |
| F | `proposals`/`advisor_journal`/`policy` の状態遷移・承認 API のサーバ側ロジック | 3 | `0006_advisor_state`・app の REST 契約 |
| G | `investigate_stock(code)` 調査パイプライン（夜=軽め／チャット=リッチ）＋ ドシエ書き込み | 4 | `0008_dossier`・data の `fetch_news`・財務 |

**着工順**: A → B → C → D（昼チャットを先に通す。Tool 1 個から）→ F → E（夜は昼の組み立てを cron で起動するだけ）→ G（Phase 4）。

---

## 1. 新規/変更ファイル一覧

### Phase 3
```
backend/app/advisor/
├── core_prompt.md           # 【変更】暫定版→本番 CORE（§2）。Tool 接続後の規律へ全面改稿
├── router.py                # 【変更】Tool ループ化・context 受け取り・policy 更新経路（§6）
├── llm.py                   # 【変更】complete() に tools 引数追加・tool_calls 返却対応（§5.1）
├── prompt_builder.py        # 【新規】1 ターンのメッセージ列を組み立てる（§4）
├── policy_compiler.py       # 【新規】policy(dict) → POLICY 自然文（§3）
├── tools/
│   ├── __init__.py          # 【新規】
│   ├── registry.py          # 【新規】Tool 定義の単一の真実（schema + dispatch）（§5.2）
│   ├── schemas.py           # 【新規】各 Tool の引数/返却 Pydantic モデル（§5.3）
│   └── handlers.py          # 【新規】LLM の tool_call → quant/data 関数へ橋渡し（§5.4）
├── nightly.py               # 【新規】軸1 夜の分析 AI（§7）
└── service.py               # 【新規】policy 更新・journal snapshot・proposal 起票の共通ロジック（§8）

backend/app/db/
├── schema.py                # 【変更】policy / advisor_journal / proposals テーブル追加（§9）
└── repo.py                  # 【変更】上記の get/upsert/list/状態遷移クエリ追加（§9）

backend/app/routers/
└── advisor_state.py         # 【新規】GET/PUT /policy・GET /journal・/proposals 系（app レーンと契約共有・§10）

backend/alembic/versions/
└── 0006_advisor_state.py    # 【新規】policy / advisor_journal / proposals（+depends_on）。発行は data-arch・定義は本レーン正本（_arbitration 決定1）
```
> **[突合 app]** `advisor_state.py` の REST 契約は app レーン #4 と共有。ルータの所在（`routers/` か `advisor/`）は app レーンに合わせる。本書は**サーバ側ロジック**（`service.py`）を主担当とし、HTTP 入出力は app に委ねる。

### Phase 4
```
backend/app/advisor/
├── dossier.py               # 【新規】investigate_stock パイプライン（§11）
└── tools/handlers.py        # 【変更】investigate_stock / get_dossier / fetch_news を追加

backend/app/db/
├── schema.py                # 【変更】watchlist / stock_dossiers / dossier_sources 追加（B-13 で本レーンが正本）
└── repo.py                  # 【変更】ドシエ・ソース台帳の upsert/get、URL 重複排除

backend/alembic/versions/
└── 0008_dossier.py          # 【新規】watchlist / stock_dossiers / dossier_sources（_arbitration 決定1・B-13）
```
> **[突合 data]** `fetch_news` の実体（昼 MCP／夜軽め）は data レーン #2。本書は**パイプラインの段取り**と**書き込みスキーマ**を主担当。

---

## 2. CORE プロンプトの骨子（`core_prompt.md` 本番版）

ADR-015 の 5 要素を本番（Tool 接続後）の規律に書き換える。**暫定版の「Tool 未接続だから数値を出せない」自己申告は全削除**し、「数値は必ず Tool 経由」へ反転させる。Markdown 見出し構成:

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

- **変更方法**: jj 版管理・意図的コミットのみ。チャット AI は触れない（ADR-015）。`router.py:26` 同様に**起動時 1 回読み込み**で運用継続。
- **[突合 quant]** 方法論②に書く手法名（モメンタム・RSI・相関・シャープ・最大DD）は quant レーンが実装する手法・指標名と一致させる。手法カタログ（§5.6）が増えたら②は「カタログ参照」に寄せる。

---

## 3. POLICY コンパイラ（`policy_compiler.py`）

DB `policy`（§9）の 1 行 dict を、システムプロンプトに差す**自然文の POLICY 層**へ整形する。ADR-013/advisor.md §3。

```python
def compile_policy(policy: dict[str, object] | None) -> str:
    """policy 行を POLICY 層の自然文に整形する（advisor.md §3）。

    policy が None（未設定）なら「方針はまだ設定されていない。対話で引き出す」旨の
    1 段落を返す（チャット初回でも壊れない）。
    数値・列はそのまま載せず、判断の制約／志向として文章化する（生データ丸投げ禁止＝ADR-014 の精神）。
    """
```

整形ルール（構造化コア → 文）:
- `risk_tolerance` / `time_horizon` → 「リスク許容度は高め・短〜中期」
- `target_cash_ratio` / `max_position_weight` / `sector_caps` → 「現金バッファ X% を尊重」「1 銘柄上限 Y%」「業種上限は素材 Z% …」
- `target_return` → 「目標リターンは高め」
- `no_leverage`（true）→ 「信用取引・レバレッジは使わない。個別銘柄の全損（ゼロカット）は受容するが借金は負わない」
- `exclusions`（JSON 配列）→ 「次は除外: …」
- `rationale`（自由文）→ そのままニュアンス・理念として末尾に差す。

- **二重活用（ADR-013）**: 同じ構造化コア（`target_cash_ratio`/`max_position_weight`/`sector_caps`/`exclusions`/`no_leverage`）は **quant レーンの `optimize_portfolio` の制約**にも渡る。POLICY 文と最適化制約は**同じ policy 行から**作る（真実は 1 つ）。
  - **[突合 quant]** `optimize_portfolio(portfolio_id)` は内部で policy を読み制約に変換する。本書は「policy → 文」、quant は「policy → 制約」。**policy のキー名と意味を共有**する（§9 の列定義が正本）。

---

## 4. プロンプト組み立て（`prompt_builder.py`）

advisor.md §6 の層を 1 つの関数に集約。**軸1・軸2 で共通**（違うのは context の有無と起動契機だけ＝ADR-011）。

```python
def build_messages(
    *,
    core_prompt: str,                       # core_prompt.md（不変）
    policy: dict[str, object] | None,        # DB policy 1 行
    conversation: list[Message],             # 軸2: 会話履歴 / 軸1: [user=夜の指示文] 1 件
    screen_context: ScreenContext | None = None,  # 軸2 のみ。軸1 は必ず None（ADR-025）
    method_cards: list[str] | None = None,   # 該当時のみ（初期は None or 全列挙・§5.6）
    recent_journal: str | None = None,       # 直近の投資日記の要約（文脈・連続性）
    facts: None = None,                      # 事実は Tool ループで動的に入るのでここでは積まない
) -> list[dict[str, object]]:
    """advisor.md §6 の順序でメッセージ列を組む。"""
```

**組み立て順序（system → 会話）**:
```
system: [CORE]
system: [POLICY]            ← compile_policy(policy)
system: [手法カード]         ← method_cards があれば（§5.6・初期は省略可）
system: [文脈]              ← recent_journal があれば「直近の投資日記: …」
system: [画面コンテキスト]    ← 軸2 のみ。compile_screen_context() の 1 行（§4.1）
...conversation（user/assistant の列）
```
- **[事実]は messages に静的に積まない**。Tool ループ（§5.5）で `tool` ロールとして会話に**動的に挿入**される。これが ADR-014 の「数値は Tool 戻り値に紐づく」を構造的に担保する。
- system を複数に分けるか 1 本に連結するかは実装裁量（OpenAI 互換 API は複数 system 可）。**CORE と POLICY の物理分離（ADR-015）はファイル/DB レベルで既に担保**されているので、プロンプト上は連結でも分離でもよい。

### 4.1 画面コンテキストの注入（軸2・ADR-025/screens.md §5）

```python
class FocusRef(BaseModel):
    type: Literal["stock", "portfolio", "signal", "proposal"]
    code: str | None = None          # 銘柄コード等。type により意味が変わる
    id: int | None = None            # portfolio_id / proposal_id 等

class ScreenContext(BaseModel):
    page: str                        # 例 "stock_detail" / "dashboard" / "signals" / "policy"
    focus: FocusRef | None = None    # 対象が無いページは省略

def compile_screen_context(ctx: ScreenContext) -> str:
    """画面 context を 1 行の自然文へ（数値は載せない）。
    例: 「ユーザーは銘柄 6920 の詳細ページを見ている」「ユーザーは Dashboard を見ている」
    """
```
- **数値・画面データは絶対に載せない**。「何の話か」のヒントのみ。AI は数値が要れば Tool で取り直す。
- **揮発情報で DB 保存しない**（送信時のみ使用）。
- **[確定・正本（_arbitration 決定3）]** `/chat` リクエスト body の `context: { page, focus?: { type, code?, id? } }` で確定。`focus` の正本は **`{ type: "stock"|"portfolio"|"signal"|"proposal", code?: string, id?: number }`**（ai-advisor 形）。**type で使い分け**: `stock`/`signal` は `code`、`portfolio`/`proposal` は `id`。app §P3-5（`ChatContext.focus`）・api.md §4 をこの形に拡張済み（DOC-4）。数値・画面データは載せない（ADR-025）。

---

## 5. Tool Calling レイヤ

### 5.1 LLM アダプタの tools 対応（`llm.py` 変更）

現状 `complete(messages) -> str` を、tool_calls を返せるよう拡張する。**運搬役の規律（ADR-015）は維持**——組み立て・dispatch は上位。

```python
class LLMResponse(BaseModel):
    content: str | None                  # テキスト応答（tool_calls 時は None のことがある）
    tool_calls: list[ToolCall]           # LLM が要求した Tool 呼び出し（空なら最終応答）

class ToolCall(BaseModel):
    id: str                              # OpenAI 互換の tool_call_id
    name: str                            # Tool 名（registry のキー）
    arguments: dict[str, object]         # パース済み引数（JSON.loads 済）

async def complete(
    messages: list[dict[str, object]],
    *,
    tools: list[dict[str, object]] | None = None,   # OpenAI tools スキーマ（registry が供給）
    stream: bool = False,                            # 将来用（現状 False 固定・TODO のまま）
) -> LLMResponse:
    """messages（＋tools）を LLM に投げ、テキストか tool_calls を返す。計算はしない（ADR-014）。"""
```
- 既存 `/chat` の戻り `{reply}` 契約は壊さない（router 側が最終 `content` を `reply` に詰める）。
- **既定モデル（確定）**: 現 `anthropic/claude-sonnet-4-6`（config.py:31）を維持（Tool Calling 対応品質帯・ADR-012 補足）。**[U-5・ユーザー裁定]** 夜間毎晩＋チャットでフルプロンプト往復の**コスト許容上限**は Phase 3 着手時に概算してユーザー確認（`_open-questions.md`）。

### 5.2 Tool レジストリ（`tools/registry.py`）— 単一の真実

各 Tool を「OpenAI tools スキーマ」＋「handler 関数」のペアで宣言する。**スキーマと実装がズレない**ようにここに集約（advisor.md §5.1 の思想を Tool にも適用）。

```python
@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str                     # LLM 向け説明（いつ呼ぶか）
    parameters: dict[str, object]        # JSON Schema（引数）
    handler: Callable[..., Awaitable[dict]]  # 実体（handlers.py）。返却は素の dict（JSON 化）
    min_phase: int                       # 投入フェーズ（1/2/3/4…）

REGISTRY: dict[str, ToolDef] = { ... }

def openai_tools(available_phase: int) -> list[dict]:
    """min_phase <= available_phase の Tool だけ OpenAI tools 配列にして返す。"""
```
- **Phase ゲート**: `min_phase` で「まだ実装されていない Tool を LLM に見せない」。Phase 3 時点で signals/indicators 系（quant が Phase 1 で実装済み）は露出、optimize 系（Phase 2）も露出、dossier 系（Phase 4）は非露出。

### 5.3 Tool の引数/返却スキーマ（`tools/schemas.py`）

**[確定・正本（_arbitration 決定2）]** 以下の返却 JSON は正本に完全一致させた。**原則**: quant の純関数が「事実」を計算 → Tool handler（このレーン）が薄く包む → app の REST 型と一致。各 Tool は handler でその関数を呼ぶだけ。

**全レーン共通の約束（正本）**:
- 比率・weight・cash_ratio・deviation の current/limit は **すべて 0..1**（DB/API/Tool）。UI でのみ ×100 して %。
- 遅延フラグは **`is_delayed: bool`**（`delayed` は廃止）。鮮度日は **`as_of: "YYYY-MM-DD"`**。
- correlation は **`{codes: string[], labels: string[], matrix: number[][]}`**（順序保証・UI 直結）。
- weights は **配列 `[{code, current_weight, target_weight, delta}]`**（dict 返しは禁止・順序安定）。

| Tool | 引数 | 返却 JSON スキーマ（正本・キー） | 計算供給 | min_phase |
|---|---|---|---|---|
| `screen_stocks` | `criteria: {signal_type?, sector33_code?, min_score?, limit?}`（**criteria キーは内部列名**） | `{date, is_delayed, items: [{code, company_name, signal_type, score, indicators}]}`（各 item は `payload` でなく `indicators`＝指標値 dict） | quant | 1 |
| `get_indicators` | `code: str` | `{code, as_of, adj_close, sma25, sma75, rsi14, vol_ma20, is_delayed}`（**平坦**。`sma5` は P1 では計算しない・ネスト `sma:{}` は不採用） | quant | 1 |
| `get_signals` | `date?: str, type?: str, code?: str` | `{date, is_delayed, signals: [{code, company_name, signal_type, score, payload}]}`（`company_name` は signals JOIN stocks＝ルータ。行レベル date は持たずトップのみ。`payload`(JSON) に `label`(短文)・`change_5d` を quant が格納＝B-6） | quant | 1 |
| `get_portfolio_metrics` | `portfolio_id: int` | `{portfolio_id, as_of, annual_return, annual_volatility, sharpe, max_drawdown, correlation: {codes, labels, matrix}, lookback_days, is_delayed, deviations: [{kind, label, current, limit, breached}]}`（current/limit は 0..1） | quant | 2 |
| `optimize_portfolio` | `portfolio_id: int` | `{portfolio_id, as_of, objective, cash_weight, weights: [{code, current_weight, target_weight, delta}], expected_annual_return, expected_annual_volatility, expected_sharpe, constraints_applied, infeasible}`（weights/cash_weight は 0..1） | quant（policy 制約適用） | 2 |
| `get_financials` | `code: str` | `{code, items: [{disclosed_date, fiscal_period, net_sales, operating_profit, profit, eps, bps}]}`（`financials` テーブル `0005`・data-arch が取得仕様を定義） | data | 2 |
| `get_asset_overview` | （なし） | `{as_of, total_value, stock_value, cash_value, external_value, pnl, is_delayed, allocation: [{name, value, weight}], policy_targets, deviations: [{kind, label, current, limit, breached}], trend}`（allocation は name 単位＝株式/現金/投信・weight は 0..1） | data/quant | 2 |
| `get_dossier` | `code: str` | `{code, summary_md, key_facts, last_investigated_at, sources: [{url, title, summary, published_at, source_type}]}` | このレーン（§11） | 4 |
| `investigate_stock` | `code: str` | `{code, summary_md, key_facts, last_investigated_at, n_sources_added}` | このレーン（§11） | 4 |
| `fetch_news` | `code: str, since?: str` | `{code, articles: [{url, title, summary, published_at, source_type}]}` | data（昼 MCP／夜軽め） | 4 |

- 全 Tool の返却は**素の dict**（registry が `json.dumps` して `tool` ロールへ）。`is_delayed`（12 週間遅延注記・api.md §0）と鮮度 `as_of` は株価依存の Tool（`get_indicators`/`get_signals`/`screen_stocks`/`get_portfolio_metrics`/`optimize_portfolio`/`get_asset_overview`）に付く——quant/data の戻りに含む。
- **[確定]** `screen_stocks.criteria` の語彙は内部列名（`signal_type`/`sector33_code`/`min_score`/`limit`）に統一（quant の signals/指標キーと一致）。
- **[確定・B-12]** policy 逸脱（`deviations: [{kind, label, current, limit, breached}]`）は **quant の単一 Python 関数が計算**し、`get_portfolio_metrics`（Tool・AI のリスク文脈）と `get_asset_overview`（画面 Dashboard）の**両方に同値を供給**（計算 1 か所・出力先 2 つ）。AI には計算させない（ADR-014）。

### 5.4 ハンドラ（`tools/handlers.py`）

各 handler は「LLM 引数（dict）→ quant/data の関数呼び出し → 返却 dict」の薄い橋渡し**のみ**。ロジック・計算は持たない（ADR-014・コーディング作法のレイヤ分離）。

```python
async def handle_get_indicators(args: dict) -> dict:
    code = GetIndicatorsArgs(**args).code        # schemas.py で検証
    return quant.compute_indicators(code)        # quant レーンの実関数（戻りはそのまま）
```
- **[突合 quant/data]** `quant.compute_indicators` 等の**実関数名・モジュール所在は quant/data レーンが決める**。本書は「handler が呼ぶ」契約だけ固定。例外は handler 内で握り、`{error: "…"}` を返して LLM に伝える（落とさない）。

### 5.5 Tool 実行ループ（dispatch・`router.py` / `service.py`）

```
1. build_messages(...) で初期メッセージ列を作る
2. resp = await complete(messages, tools=openai_tools(current_phase))
3. while resp.tool_calls:
     for tc in resp.tool_calls:
         result = await REGISTRY[tc.name].handler(tc.arguments)   # 未知 name は {error} を返す
         messages.append(assistant tool_call 記録)                 # OpenAI 形式
         messages.append({role:"tool", tool_call_id: tc.id, content: json.dumps(result)})
     resp = await complete(messages, tools=...)
     # 安全弁: ループ上限（例 max 6 往復）。超過したら打ち切り最終応答を促す
4. return resp.content   # 最終テキスト
```
- **[確定・正本（_arbitration 決定3・L-16）]** 各 Tool 実行は UI に可視化（screens.md §4「⚙ get_signals 実行」）。**Phase 3 は非ストリーミング**のまま、`ChatResponse` に **`tool_runs: [{name, args?}]`**（app 正本・`tool_calls_made: string[]` は廃止）を足して UI に出す。**結果の数値は載せない**（ADR-025）。SSE は Phase 3 後に検討（api.md §7）。

### 5.6 手法カード（§advisor.md §5・初期は省略可）

- **初期はベタ書き list で十分**（手法が数個）。`method_cards` テーブル・embedding は作らない（ADR-016・data-model.md §5）。
- Phase 3 では `method_cards=None`（手法カード層を差さない）で開始してよい。手法が増えたら `prompt_builder` の `method_cards` 引数に全列挙 → 将来 RAG（`sqlite-vec`）。
- **計算は持たない**（ADR-016）。カードは「どの手法を使うか」の索引＋参照知識のみ。

---

## 6. 軸2 相談チャット（`router.py` 改修）

現状（CORE 差し込みのみ・ステートレス）を、Tool ループ＋context 注入へ。

```python
class ChatRequest(BaseModel):
    messages: list[Message]
    context: ScreenContext | None = None     # ADR-025（§4.1）。app レーンと契約共有

class ToolRun(BaseModel):
    name: str                                # 呼んだ Tool 名
    args: dict[str, object] | None = None    # 引数（結果の数値は載せない＝ADR-025）

class ChatResponse(BaseModel):
    reply: str
    tool_runs: list[ToolRun] = []            # UI 可視化用（§5.5・正本 = app 形）

@router.post("/chat")
async def chat(req: ChatRequest) -> ChatResponse:
    policy = repo.get_policy(conn)
    recent = repo.get_recent_journal_summary(conn)     # 直近 journal（文脈）
    messages = build_messages(core_prompt=_CORE, policy=policy,
                              conversation=req.messages, screen_context=req.context,
                              recent_journal=recent)
    reply, tool_runs = await run_tool_loop(messages, phase=current_phase())
    return ChatResponse(reply=reply, tool_runs=tool_runs)
```

### 6.1 会話履歴の扱い（現状ステートレスの是正方針）

- **現状**: サーバはステートレス・frontend が毎ターン全 messages 送信（_current-state.md §6・ADR-024 は「永続実体は実装時に決める」）。
- **方針**: Phase 3 は**ステートレス維持を既定**とする（frontend が会話を保持・送る）。サーバは会話を DB 保存しない。
  - 理由: ADR-024 が会話保持を frontend（localStorage）に置く前提。会話の DB 永続は単一ユーザーに過剰で、`advisor_journal`（方針の履歴）とは別物。
  - **[U-6・ユーザー裁定]** 会話履歴の**永続先**（リロードで消えてよいか／DB 保存するか）。**既定: 当面 localStorage のみ（消えてよい）。`policy` の変更は journal に必ず snapshot されるので、重要な決定は失われない**（`_open-questions.md`）。
- **トークン肥大対策**: 会話が長い場合の要約・truncate は **Phase 3 は素朴に全送信、肥大が問題化したら server 側で古い turn を要約**（lead 裁量・後付け可）。

### 6.2 チャットでの policy 更新 → journal snapshot

- ユーザーと合意して方針が変わる場合、AI は **`update_policy` を直接 Tool で叩かない**（チャットが CORE/方針を勝手に書き換えない規律）。代わりに**提案として `proposals`（kind=`policy_change`）を起票**し、ユーザーが承認して初めて `policy` 更新＋journal snapshot（§8）。
  - **[U-7・ユーザー裁定]** 「チャット内で即 policy 更新（承認 UI を介さず）」を許すか。**既定: 承認制に寄せる（proposals 経由）**。ADR-013 の「対話で気軽に育てる」と ADR-018 の承認制の折衷——**軽微な `rationale` 追記はチャット即時更新を許し、構造化コア（上限・除外・no_leverage）の変更は proposals 承認制**（`_open-questions.md`）。

---

## 7. 軸1 夜の分析 AI（`nightly.py`）

cron 夜間バッチ（Phase 1 で導入・data レーン）に相乗り。**画面 context 無し**（ADR-025）。

```python
async def run_nightly_advisor(conn: Connection) -> None:
    """その日の事実を集め、advisor_journal を 1 件生成し proposal を起票する（ADR-011/018）。

    1. policy = repo.get_policy(conn)
    2. briefing = collect_situation_briefing(conn)     # 今日の signals / portfolio / 資産 / 指数（dict）
    3. messages = build_messages(core_prompt=_CORE, policy=policy,
           conversation=[user: 夜の定型指示],  # 「昨日までの方針と今日の事実を突き合わせ、見直しを提案せよ」
           screen_context=None,                # 軸1 は画面が無い（ADR-025）
           recent_journal=repo.get_recent_journal_summary(conn))
    4. reply, tools_used = await run_tool_loop(messages, phase=current_phase())
       # 夜も同じ Tool を使える。get_signals/get_portfolio_metrics 等で事実を取り直す。
    5. parsed = parse_advisor_output(reply)             # observations / proposal / proposed_policy_change
    6. journal_id = repo.insert_journal(conn, date, briefing(JSON), observations, proposal,
           proposed_policy_change, policy_snapshot=policy(JSON), llm_model=settings.llm_model)
    7. if parsed.proposed_policy_change:
           repo.insert_proposal(conn, kind="policy_change", body=…, rationale=…,
               status="pending", journal_id=journal_id)
    8. 失敗時（LLM タイムアウト等）はリトライ→ダメなら日記スキップして記録し Discord 通知（§7.1）
    """
```

- **collect_situation_briefing**: Tool と同じ事実取得関数を呼んで dict に集約（quant/data 供給）。`advisor_journal.situation_briefing` に JSON で保存（監査・後から「何を見て判断したか」を辿る）。
- **proposed_policy_change の構造化（確定・L-17/決定7）**: 出力を**専用 Tool `submit_journal(observations, proposal, proposed_policy_change?)` で受ける**（JSON 文字列パースより堅い・ADR-014 と整合）。`proposed_policy_change` の形は `{field, from, to, reason}`。この Tool は registry に min_phase=3 で登録（夜の最終ターンで LLM が呼ぶ）。

### 7.1 障害時（ADR-018）

- LLM 呼び出しはリトライ（指数バックオフ・上限あり）。ダメなら**その日の journal をスキップ**して「失敗を記録」（空 journal or skip フラグ）し、`DISCORD_WEBHOOK_URL` へエラー通知。
- signals は前日分が残る（夜の分析失敗が data 取得を壊さない）。
- **[確定]** Discord 通知ユーティリティ（DiscordAdapter・data-arch §`adapters/` 管轄）を使い、**夜の分析の失敗エラー通知だけ Phase 3 で最小実装**（ADR-018 は無人運用の前提・本格通知は Phase 6）。

---

## 8. 状態遷移（`service.py`）— policy / advisor_journal / proposals

```python
def apply_policy_change(conn, *, change: dict, source: Literal["chat","nightly"],
                        journal_id: int | None) -> None:
    """policy を更新し、その日の advisor_journal に policy_snapshot を残す（ADR-013）。"""

def resolve_proposal(conn, proposal_id: int, *, decision: Literal["approved","rejected"],
                     outcome: str | None = None) -> None:
    """proposals.status を遷移。approved かつ kind=policy_change なら apply_policy_change を呼ぶ。
    depends_on が指す提案が未 approved の間は approve を弾く（承認順制御・B-8/決定4）。"""
```

**遷移図**:
```
proposal: pending ──approve──▶ approved ──(policy_change なら)──▶ apply_policy_change → journal.policy_snapshot 更新
                  └─reject───▶ rejected
          ※ depends_on(別 proposal) が未 approved の間は approve 不可（policy_change→buy の順序を守る）
policy: 単一行を更新（版管理機構なし・ADR-013）。変更のたび当日 journal に snapshot。
advisor_journal: 夜=1件/日 自動生成。チャットで policy 変更時も当日 journal に snapshot＋理由を残す。
proposals.journal_id: 夜の proposal は生成元 journal に紐付け。チャットの proposal は journal_id=null 可。
```
- **buy/sell/rebalance の承認は約定を起こさない**（screens.md §6(c)・ADR-001）。`status` 上の状態遷移のみ。承認時に「取引記録の入力を促す」かは UI 詳細（app レーン）。
- **[突合 app]** `/proposals/{id}/approve|reject`（api.md §4）の HTTP は app、本書の `resolve_proposal` がサーバ側実体。

---

## 9. テーブル追加（`schema.py` / `repo.py`）— Phase 3・**Alembic `0006_advisor_state`**

data-model.md §5 に準拠。リビジョンは **`0006_advisor_state`（down_revision=`0005_financials`）= ai-advisor がこの 3 テーブルの正本**（_arbitration 決定1 の通し番号表）。発行（ファイル作成）の一元管理は data-arch だが、定義内容は本レーンが正本。schema 一元管理は data レーンと衝突しないよう**同じ `metadata` に追加**（_current-state.md §1）。

**[確定・B-9（_arbitration 決定5）]** ADR-002 の「書き手」解釈: **DB に触れる OS プロセスは FastAPI 1 つだけ**（ADR-005）。夜間バッチは APScheduler で FastAPI プロセス内に同居（data-arch 方式C）するので、`policy`/`proposals` の昼書き込み（チャット/承認）と夜バッチ書き込みは**同一プロセス内で直列化**され、クロスプロセスの書×書競合は原理的に起きない。稀な競合は SQLite `busy_timeout`（例 5000ms）で吸収。→ ADR-002/data-model.md に補注（DOC-9・data-arch 主管）。

- **`policy`**（1 行運用）: `id` / `risk_tolerance` / `time_horizon` / `target_cash_ratio`(REAL・**0..1**) / `max_position_weight`(REAL・**0..1**) / `sector_caps`(TEXT=JSON・値は **0..1**) / `target_return`(REAL) / `no_leverage`(INT 0/1) / `exclusions`(TEXT=JSON) / `rationale`(TEXT) / `updated_at`(TEXT)
  - 比率系（`target_cash_ratio`/`max_position_weight`/`sector_caps`）は **0..1**（_arbitration 決定2 の横断ルール）。`optimize_portfolio` の制約にそのまま渡るため UI で ×100 する（DB は 0..1）。
- **`advisor_journal`**: `id` / `date` / `situation_briefing`(TEXT=JSON) / `observations`(TEXT) / `proposal`(TEXT) / `proposed_policy_change`(TEXT=JSON) / `policy_snapshot`(TEXT=JSON) / `llm_model`(TEXT) / `created_at`(TEXT)
- **`proposals`**: `id` / `created_date` / `kind`(`policy_change`/`buy`/`sell`/`rebalance`) / `body`(TEXT=JSON) / `rationale`(TEXT) / `status`(`pending`/`approved`/`rejected`) / `outcome`(TEXT) / `resolved_at`(TEXT) / `journal_id`(INT・FK→advisor_journal・nullable) / **`depends_on`(INTEGER NULL・FK→proposals.id)**（B-8/決定4・承認順制御 policy_change→buy）。index: `status`。

repo 関数（素の dict 返し・コーディング作法）:
```python
get_policy(conn) -> dict | None
upsert_policy(conn, fields: dict) -> None             # 1 行運用（id 固定 or is_active=1）
insert_journal(conn, **fields) -> int                  # journal_id を返す
get_recent_journal_summary(conn, n: int = 1) -> str | None
list_journal(conn, from_=None, to=None) -> list[dict]
insert_proposal(conn, **fields) -> int
list_proposals(conn, status: str | None = None) -> list[dict]
get_proposal(conn, id: int) -> dict | None
update_proposal_status(conn, id, status, outcome=None, resolved_at=…) -> None
```

## 10. REST（`advisor_state.py`）— app レーンと契約共有

api.md §4 準拠。**[突合 app]** HTTP 入出力は app #4。本書はサーバ側ロジック（§8）と Pydantic 形だけ提示。
- `GET /policy` → 構造化コア＋`rationale` を**分けて**返す（api.md §7・screens.md §3）。`PUT /policy` → `upsert_policy`＋journal snapshot。
- `GET /journal?from=&to=` → `list_journal`。
- `GET /proposals?status=` → `list_proposals`。`POST /proposals/{id}/approve|reject` → `resolve_proposal`。

---

## 11. Phase 4 — `investigate_stock(code)` 調査パイプライン（`dossier.py`）・**Alembic `0008_dossier`**

ADR-020。**1 本のパイプラインを夜間バッチ（watchlist 巡回・軽め）とチャット Tool（リッチ）の両方から呼ぶ**。

**[確定・B-13（_arbitration 決定1）]** Phase 4 の **`0008_dossier`（down_revision=`0007_screening`・当初計画 0007 は ADR-031 screener の割り込みで繰り下げ）で `watchlist` / `stock_dossiers` / `dossier_sources` の 3 テーブルを ai-advisor が一括定義（正本）**。watchlist は data-arch（旧 Phase 2 `0003`）から外し、ここに一本化した（app の watchlist API も Phase 4・data-model.md/roadmap も Phase 4 で整合）。

```python
async def investigate_stock(conn, code: str, *, mode: Literal["nightly","chat"]) -> dict:
    """個別銘柄を調査しドシエを生成・更新する（ADR-020）。

    段階（取得→要約→保存）:
    1. financials = data.get_financials(code)            # 財務（J-Quants Free・data レーン）
    2. articles = await fetch_news(code, since=今日-7日, mode=mode)
         # mode=chat: 昼 MCP（playwright/fetch）でリッチ / mode=nightly: MCP 非依存で軽め（ADR-020）
         # 発行 1 週間以内・data レーンが取得手段を mode で切替
    3. new_articles = [a for a in articles if not repo.dossier_source_exists(conn, a.url)]  # URL UNIQUE で重複排除
    4. for a in new_articles:
         repo.upsert_dossier_source(conn, code=code, url=a.url, title=a.title,
             summary=a.summary, published_at=a.published_at, source_type=a.source_type)
         # 本文は保存しない（要約＋URL のみ・ADR-020）
    5. existing = repo.get_dossier(conn, code)            # 既存 summary_md（living document）
    6. summary_md, key_facts = await summarize_dossier(   # LLM で要約更新（既存＋財務＋新ソース要約）
            existing, financials, new_articles)           # ※本文ではなく「要約の積み上げ」を渡す
    7. repo.upsert_dossier(conn, code=code, summary_md=summary_md, key_facts=key_facts(JSON),
            last_investigated_at=now, updated_at=now)
    8. return {code, summary_md, key_facts, last_investigated_at, n_sources_added: len(new_articles)}
    """
```

- **`summarize_dossier`**: LLM 呼び出し（CORE の規律を継ぐ・定性要約なので Tool ループ不要の単発 complete でよい）。**記事全文は渡さず**、ソースの**短い要約**を渡して既存ドシエを更新（living document）。
- **`stock_dossiers`**（1 銘柄 1 行）: `code` PK / `summary_md` / `key_facts`(JSON) / `last_investigated_at` / `updated_at`。
- **`dossier_sources`**: `id` / `code` / `source_type`(`news`/`disclosure`/`twitter`) / `url`(**UNIQUE**) / `title` / `summary` / `published_at` / `processed_at`。index: `url`(UNIQUE)・`code`。
- **`watchlist`**（夜の巡回対象・data-model.md §3・**`0008_dossier` で本レーンが正本定義**）: `id` / `code`(FK→stocks) / `note` / `added_at` / **UNIQUE(code)**（重複監視防止・data-arch 旧案から引き継ぎ）。一覧は `stock_dossiers.last_investigated_at` を join して「最終調査日」表示（screens.md・roadmap P4）。stale しきい値 **21 日**（L-22・backend 算出）。
- 夜間: watchlist を巡回し `investigate_stock(code, mode="nightly")`。**[突合 data]** 巡回の cron 組み込み・`fetch_news` の mode 別実体は data レーン。
- **[U-8・ユーザー裁定]** 夜間の調査でも LLM 要約コストが乗る（watchlist N 銘柄 × 毎晩）。**既定: 夜は「`last_investigated_at` が古い順に 1 晩 N 件」上限制で回す・stale しきい値 21 日（L-22）**。N と頻度（毎晩／週次）はユーザー確認（`_open-questions.md`）。

---

## 12. テスト方針

- **policy_compiler**: policy dict（全部入り／None／部分欠損）→ 期待文に主要トークンが含まれるか（exact match でなく contains で脆くしない）。
- **prompt_builder**: 層の順序（CORE→POLICY→手法→文脈→context→会話）と、軸1 で `screen_context=None` が強制されること。事実が静的に積まれないこと。
- **Tool registry/dispatch**: `openai_tools(phase)` の Phase ゲート（min_phase 超は出さない）。未知 Tool 名で `{error}` を返しループが落ちないこと。handler は quant/data 関数を**モック**して橋渡しのみ検証（実計算はそのレーンのテスト）。
- **Tool ループ**: tool_calls あり→handler 実行→tool ロール挿入→再 complete、の往復と**ループ上限**で打ち切ること（LLM はモック）。
- **状態遷移（service）**: pending→approved（policy_change なら policy 更新＋journal snapshot）／→rejected。buy 承認が約定を起こさないこと。
- **nightly**: LLM 失敗時に journal スキップ＋Discord 通知が呼ばれること（LLM/Discord はモック）。
- **dossier**: URL 重複排除（既存 url は upsert されない）・本文を保存しない・`last_investigated_at` 更新。LLM/fetch_news はモック。
- DB は既存方針どおり**一時 SQLite**で回す（_current-state.md・conftest）。LLM/外部は必ずモック（ネットを叩かない）。

---

## 13. [OPEN] 一覧（R3 後の状態）

R3 で大半は _arbitration メモで確定済み。残るユーザー裁定（U-）は `_open-questions.md` 行きで、spec は推奨値を既定として書く（後から env/policy/設定で差し替え可）。

| # | 論点 | R3 後の状態 |
|---|---|---|
| 1 | 既定 LLM モデル（config `anthropic/claude-sonnet-4-6`） | **確定**: 維持（Tool 品質帯・ADR-012・L 群外） |
| 2 | コスト許容上限（夜間毎晩＋チャット往復） | **[U-5・ユーザー裁定]** Phase 3 着手時に概算（`_open-questions.md`） |
| 3 | 会話履歴の永続先 | **[U-6・ユーザー裁定]** 既定 localStorage（policy 変更は journal で残る） |
| 4 | チャットでの policy 即時更新 vs 承認制 | **[U-7・ユーザー裁定]** 既定: rationale 即時／構造化コアは proposals 承認制 |
| 5 | Tool 実行の UI 伝達（SSE か最終応答同梱か） | **確定（L-16・決定3）**: Phase 3 非ストリーミング＋`tool_runs:[{name,args?}]` 同梱 |
| 6 | 夜間 Discord 通知を P3 で入れるか | **確定（L 群準拠）**: エラー通知のみ P3 で最小実装（ADR-018） |
| 7 | 夜間ドシエの調査頻度・watchlist 件数上限 N | **[U-8・ユーザー裁定]** 既定: 古い順に 1 晩 N 件・stale 21 日（L-22） |
| 8 | journal 構造化を JSON パースか submit_journal Tool か | **確定（L-17・決定7）**: `submit_journal` Tool で受ける |
| 9 | screen focus の型 | **確定（決定3）**: `{type, code?, id?}`（type で code/id 使い分け・api.md §4 拡張済 DOC-4） |

## 14. [DOCS要修正]（R3 で正本に反映済み・lead 統合の対象）
- **DOC-4 / api.md §4** `/chat` body に `context` 明記＋`focus: { type, code?, id? }` に拡張（決定3）。本書 §4.1 と一致。
- **DOC-9 / ADR-002 or data-model.md** 「書き手の系統（夜バッチ／昼手入力／チャット承認）は FastAPI 1 プロセス内同居で直列化＋busy_timeout」を補注（決定5・data-arch 主管）。本書 §9 と一致。
- **DOC-12 / roadmap・data-model.md** `watchlist` の所属を Phase 4（ai-advisor `0008_dossier`）に確定（決定1・B-13）。本書 §11 と一致。
