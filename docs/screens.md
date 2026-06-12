# Screens（画面設計・IA）

AssetVane の画面構成（情報設計）、ナビゲーション、Dashboard のレイアウト、全ページ常駐の Advisor チャットをまとめる。
個別の設計判断の「なぜ」は [decisions.md](decisions.md) を参照。

> Dashboard をハブに、ヘッダーメニューで各機能へ遷移する。主役は **Dashboard ＋ 全ページ常駐の Advisor チャット**。画面は Phase 進行に合わせて増える（[roadmap.md](roadmap.md)）。

---

## 1. 画面一覧（IA）

| # | ページ | 役割・主な中身 | 主に使う API | 投入 Phase |
|---|---|---|---|---|
| 1 | **Dashboard（ホーム）** | 資産概要・配分・AI 当日提案（承認待ち）・現在の方針・今日の signals・watchlist 再調査・投資日記。各機能への入口 | `/asset-overview` `/signals` `/proposals` `/policy` `/journal` | 0→育つ |
| 2 | **Stocks / 銘柄一覧** | 銘柄検索・絞り込み | `/stocks` | 0 |
| 3 | **銘柄詳細** | 株価チャート・テクニカル指標・財務・**ドシエ**・watchlist 追加を**集約するハブ** | `/quotes/{code}` `/stocks/{code}` `/dossiers/{code}` | 0（チャート）→4 |
| 4 | **Signals（Trend Vane）** | momentum / volume_spike の一覧スクリーニング | `/signals?type=` | 1 |
| 5 | **Portfolio** | 保有・相関ヒートマップ・最適比率・バックテスト・資産推移 | `/holdings` `/portfolio/{id}/metrics` `/portfolio/{id}/optimize` `/asset-overview` | 2 |
| 6 | **入力（Portfolio 内タブ）** | 取引（買い/売り）記録・現金・投信入力。**独立ページではなく Portfolio の「入力」タブ**（OPEN-D・§2）。`/portfolio?tab=input` で直接着地 | `/transactions` `/cash` `/external-assets` | 2 |
| 6b | **履歴（Portfolio 内タブ）** | 取引一覧（新しい順・会社名付き）・行のインライン編集・削除。**Portfolio の「履歴」タブ**（`/portfolio?tab=history`）。新規追加は #6「入力」タブのまま（追加フォームの二重化を避ける）。取引を直すと holdings は自動再導出（[ADR-019](decisions.md)）| `/transactions`（GET/PUT/DELETE）| 2 |
| 7 | **Advisor（チャット）** | 軸2。方針を対話調整・銘柄/比率提案。**実体は全ページ常駐のフローティング**（§4） | `/chat` | 3 |
| 8 | **Policy（投資方針）** | 現在の方針を表示・編集（構造化コア＋rationale） | `/policy` | 3 |
| 9 | **Journal（投資日記）** | 夜の分析AI の日記＋方針スナップショット履歴 | `/journal` | 3 |
| 10 | **Proposals（提案）** | pending/approved/rejected の消し込み・振り返り | `/proposals` `/proposals/{id}/approve` `/reject` | 3 |
| 11 | **Watchlist** | 監視銘柄一覧（**最終調査日**つき）・調査起動 | `/watchlist` `/dossiers/{code}/investigate` | 4 |
| 12 | **Alpha Scorer** | 決算スコアランキング | `/signals?type=ai_alpha` | 5 |
| 13 | **Lead-Lag（Dashboard 内 widget）** | 翌日強含む日本業種ランキング＋検証メタ。**専用ページではなく Dashboard の `LeadLagWidget`** として実装（[ADR-039](decisions.md)/[ADR-040](decisions.md)）| `/lead-lag` | 7 |
| 14 | **Settings / System** | health・必須 env チェック・夜間バッチ手動起動 | `/health` `/batch/run` | 0→ |
| 15 | **News（統合ニュース）** | 統合コーパスの一覧（`level` 単一タブ＋期間フィルタ）・「ニュースを貼る」投入フォーム・`source='user'` 行の削除。検索ボックスは [ADR-045](decisions.md) 送り（未実装） | `/news`（GET/POST/DELETE） | 4 |
| 16 | **US Stocks（米株スクリーナー）** | 米株バリュエーションの絞り込み一覧＋銘柄詳細 `/us-stocks/[symbol]`（チャート・指標）。提示専用（[ADR-055](decisions.md)）| `/us-stocks/screen` `/us-stocks/{symbol}` `/us-quotes/{symbol}` | 7 |

> 銘柄詳細（#3）は「チャート＋指標＋財務＋ドシエ＋watchlist 追加」を 1 ページに集約するハブ。ドシエ（#11 由来）は独立ページではなく**銘柄詳細内のセクション/タブ**として見せる。

> News（#15）は統合コーパス（[ADR-044](decisions.md)）の単一の顔（一覧＋貼付フォーム＋user 行削除＝[ADR-046](decisions.md)/[ADR-047](decisions.md)）。Dashboard の `GeneralNewsWidget`（[ADR-034](decisions.md)・市況の "ちら見"）は**別物として温存**し、News ページに全面置換しない。

---

## 2. ナビゲーション方針

- **ヘッダー常設**。ロゴ（AssetVane）＋ナビ＋右上に「Free・株価12週遅延」バッジ（[ADR-008](decisions.md)）。
- ナビ項目は **Phase 進行で増える**。未投入の機能を最初から全部並べない。
  - **Phase 0**: Dashboard / Stocks / Settings
  - 以降: Signals(P1) → Portfolio(P2) → Advisor・Policy・Journal・Proposals(P3) → Watchlist・News(P4) → Alpha Scorer(P5) → US Stocks(P7)。Lead-Lag は専用ナビ項目ではなく Dashboard widget(P7)
- 各項目に **Phase バッジ**（例 `P2`）を付け、「いつ有効になるか」を可視化してもよい。
- **評価額の遅延注記**は Dashboard・Portfolio・銘柄詳細の評価額/PL に横断的に必要。共通バッジ/バナーで明示する（Free は約 3 か月前の値＝[api.md](api.md)）。

---

## 3. Dashboard の構成

朝いちばんに見る画面。**承認待ちの提案**を主役に置く。

| 区画 | 中身 | データ |
|---|---|---|
| **資産概要** | 総資産・含み益・株式/現金/投信/損益・資産推移スパークライン。**12週遅延注記**つき | `/asset-overview` |
| **配分ドーナツ** | 株式/現金/投信の割合。**policy 目標との対比**と**逸脱表示**（例「最大銘柄比率 18.2% / 上限15%」を警告色） | `/asset-overview` ＋ `/policy` |
| **夜の分析AI 提案** | 承認待ち proposals を承認/却下できる。**提案間の依存**（例: policy 変更 → buy）も表現 | `/proposals?status=pending` |
| **現在の投資方針** | **rationale（自由文の理念）と構造化コア（リスク/時間軸/現金目標/1銘柄上限/目標リターン/レバレッジ）を分けて表示**。編集は**チャット経由**（「チャットで調整 →」） | `/policy` |
| **今日のシグナル** | Trend Vane（momentum / volume_spike）の上位 | `/signals` |
| **Watchlist 調査ステータス** | 最終調査日。古いものは警告色＋「再調査」 | `/watchlist`（最終調査日つき） |
| **投資日記** | 最新の所見（policy カードと被るスナップショットチップは出さない） | `/journal` |
| **Lead-Lag** | 翌日強含む日本業種ランキング（`LeadLagWidget`・[ADR-039](decisions.md)）| `/lead-lag` |
| **一般ニュース** | 市況・マクロの "ちら見"（`GeneralNewsWidget`・[ADR-034](decisions.md)）| `/general-news` |

> **現在の policy** と **日記の `policy_snapshot`** は役割が違う。前者＝*今アクティブな方針*、後者＝*その日の履歴*。Dashboard は前者を出す（[data-model.md](data-model.md) の `policy` 単一 ＋ `advisor_journal.policy_snapshot` で履歴、という設計と一致）。
>
> **policy の見せ方**: `rationale`（自由文）は引用調、構造化コアはチップ/グリッドで見せ方が違う。`GET /policy` のレスポンスでこの 2 つが区別して返る形が望ましい（[api.md §7](api.md)）。

---

## 4. 常駐 Advisor チャット（フローティング）

相談チャットAI（軸2）は、専用ページではなく**全ページ共通のフローティング UI**として常駐する（[ADR-024](decisions.md)）。

- **全ページ常駐**: 右下のフローティングボタンから開閉。**Next.js の root layout に置き、ページ遷移しても会話が消えない**（ルート変更でアンマウントしない）。
- **操作**: ヘッダーを掴んで**ドラッグ移動**、角ハンドルで**リサイズ**、**最小化**、閉じる。実装は `react-rnd` 等で足りる。
- **状態保持**: 会話と窓の位置/サイズはクライアントで保持（`localStorage` 等）。会話履歴の永続実体（保存先）は実装時に決める。
- **Tool 実行の可視化**: 「⚙ get_signals 実行」のように、AI が呼んだ Tool を UI に出す。「AI は計算せず Tool の事実で答える」（[ADR-014](decisions.md)）が見えるようにする。
- **画面を見ながら相談**: ユーザーは Dashboard の数字や調査結果を見ながら質問できる。指示語の解決は §5。

---

## 5. 画面コンテキスト注入

「画面を見ながら相談」で「**これ**調査して」「**この**集中度どう？」のような**指示語**を解くため、チャット（`/chat`）に「ユーザーが今見ているもの」を**軽量に**渡す（[ADR-025](decisions.md)・[advisor.md §6.1](advisor.md)）。

- **粒度は「ページ＋主対象」だけ**。
  ```
  page: stock_detail
  focus: { type: "stock", code: "6920" }   # 対象が無いページ（Dashboard 等）は focus 省略
  ```
- **数値・画面データは渡さない**。コンテキストは「何の話か」のヒント。AI は数値が要るなら該当 Tool（`get_signals(6920)` 等）を呼んで**事実を取り直す**（＝「必要に応じて参照できる状態」）。
- **揮発情報で DB には保存しない**。送信時のみ使う。
- 理由（トークン肥大の回避＋[ADR-014](decisions.md) の規律維持）は [ADR-025](decisions.md)。

---

## 6. 未解決の論点（記録・備忘）

画面に落として浮かんだ、**まだ決めていない**設計上の宿題。決定ではなく記録として残す。

- **(a) 提案間の依存**: `proposals` テーブルに「この提案は別の提案の承認が前提」を表す列が無い（例: policy 変更 → buy）。承認順序の制御が要るなら列追加を検討（[data-model.md](data-model.md)）。
- **(b) 逸脱の計算主体（確定）**: 「現状 vs policy の逸脱」（最大銘柄比率 18.2% / 上限15% 等）は **Python が事実として計算**する（AI に計算させない＝[ADR-014](decisions.md)）。**計算は quant の単一関数（1 か所）が行い、出力先は 2 つ** — 画面用は `/asset-overview.deviations`、AI のリスク文脈用は Tool `get_portfolio_metrics.deviations` に**同じ値を供給**する（`_arbitration.md` 決定6・[DOC-5]）。逸脱の `current`/`limit` は 0..1（UI でのみ %）。
- **(c) Buy 提案承認の扱い（取引機能は持たない・方針確定）**: AssetVane は**発注・取引実行機能を持たない**（自動売買せず提示に徹する＝[ADR-001](decisions.md)・README）。Buy 提案の「承認」は `proposals.status` 上の状態遷移にすぎず、**約定を起こさない**。ユーザーは自分で発注し、約定したら `transactions` に手入力する（[ADR-019](decisions.md)）。残る UI 詳細は「承認時に取引記録の入力を促すか」程度。
- **(d) Dashboard の集約**: `/asset-overview` 1 本に総資産・配分・逸脱・推移を集約しすぎる懸念。分割か集約エンドポイントかは Phase 2 着手時に決める（[api.md](api.md)）。

---

## 7. モック

設計イメージ用の動くモック（ダミーデータ・配線なし）は **`.tmp/mockups/dashboard.html`** にある。**[DESIGN.md](../DESIGN.md) のトークン**（青アクセント単一・surface lift＋罫線・密度優先・pill 廃止・tnum 数値・**sidebar 220px ＋ topbar 48px** のシェル）に準拠した版を採用した。

> `.tmp/` は作業用一時ディレクトリで **git 管理外**（`.gitignore`）。単一ユーザー前提のスクラッチ置き場で、設計の真実は本 docs 側にある。
