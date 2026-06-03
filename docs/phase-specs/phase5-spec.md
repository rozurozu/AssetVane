# Phase 5 着工仕様: AI Alpha Scorer（決算スコアリング）
> 出所: roadmap.md Phase 5 / ADR-006(学習別PC・推論のみ)/ADR-016(手法はコード)。レビュー・裁定反映済み。コード未実装＝着工仕様。
>
> 合成元（Phase 5 該当部分の抽出・裁定済み）: `docs/phase-specs/_drafts/_arbitration.md`（正本）・`quant.md` §5・`data-arch.md` §5・`ai-advisor.md` §Tool 一覧・`_current-state.md`・`docs/roadmap.md` Phase 5・`docs/decisions.md` ADR-006/ADR-016/ADR-021・`docs/phase-specs/_open-questions.md` U-4。
> 単位の約束（_arbitration 決定2・B-3）: 比率・weight・score はすべて **0..1**（UI でのみ ×100）。遅延フラグは `is_delayed: bool`、鮮度日は `as_of: "YYYY-MM-DD"`。
> このレーンは **quant（特徴量・学習・推論ロジック）** と **data-arch（.pkl 配置・読込・バージョニング・推論ジョブのバッチ相乗り）** の合流仕様。本書はその合成。
> 前提する既存資産: `signals` テーブル（`0003_signals`・Phase 1・**ai_alpha は既存 DDL を流用し新テーブルを作らない**）／`financials` テーブル（`0005_financials`・Phase 2・data-arch が供給）／`index_quotes`（`0004`・ベンチ＝対 TOPIX 超過リターン算出用）。

---

## 0. 目的と完了条件

**目的**: 決算（`financials`）＋将来リターンで学習した LightGBM のスコアを、Advisor の材料（事実）に加える。AI には計算させず、Python が事実（スコア）を出し、LLM は `get_signals(type=ai_alpha)` 経由で受け取って解釈・提案する（ADR-014）。

**完了条件**（roadmap.md Phase 5）:
1. 別 PC で学習した `model.pkl` をラズパイにコピーし、推論ジョブが全銘柄スコアを算出して `signals`（`signal_type='ai_alpha'`）へ UPSERT できる。
2. スコアランキングが REST（`GET /signals?type=ai_alpha`）と画面（`/signals` ランキング）で表示される。
3. AI Advisor が `get_signals(type=ai_alpha)` でスコアを根拠に使える（ai-advisor.md §Tool 一覧 行 `get_signals`）。
4. **学習の再現手順が `docs/ml-training.md` にドキュメント化されている**（ADR-006 の完了条件・§3.5）。
5. 推論の決定性が担保される（同じ `.pkl`＋同じ特徴量 → 同じ score＝ADR-016・§9）。

**このフェーズで作らないもの（スコープ外）**: ラズパイでの学習（ADR-006 で禁止＝別 PC）／自動モデル配布（手動 rsync で足りる・YAGNI）／Black-Litterman 等の高度化／lead_lag（Phase 7）。

---

## 1. 全体像（学習=別PC・ラズパイ=推論のみ・前提する financials(0005)/signals(0003)）

ADR-006 の厳守。役割を 2 系統に割る。

```
[別 PC（学習・低頻度・高負荷）]
  financials(0005) + daily_quotes + index_quotes  ──point-in-time抽出──▶  特徴量X + ラベルy
  └▶ backend/app/quant/ml/train.py        # LightGBM 学習・CV・評価
     └▶ 出力: ai_alpha-<YYYY-MM-DD>.pkl + .json（メタ）   # joblib
        └▶ rsync で ラズパイの backend/models/ へコピー（手動・git 管理外）

[ラズパイ（推論のみ・夜間バッチ相乗り・軽量）]
  backend/app/ml/model_store.py: load_active("ai_alpha")  # *-latest.json → .pkl を joblib.load + メタ検証
  backend/app/quant/ml/infer.py: score_all(...)           # 全銘柄の特徴量を point-in-time 組立 → predict → ランク正規化
  └▶ backend/app/batch/jobs/score_ai_alpha.py（NIGHTLY_JOBS に追加）
     └▶ repo.upsert_signals(rows)  # signal_type='ai_alpha'・既存 signals 表（0003）へ UPSERT（冪等）
        └▶ GET /signals?type=ai_alpha・get_signals(type=ai_alpha)・/signals ランキング画面
```

- **DB に触れる OS プロセスは FastAPI 1 つ**（ADR-005）。推論ジョブは APScheduler 同居の `run_nightly` 内の 1 ジョブ（data-arch 方式 C・`_arbitration` 決定5）。書き込みは UPSERT で冪等（ADR-002）。
- **quant モジュール（`quant/ml/`）は DB を知らない**（純関数で dict/DataFrame を返す）。DB I/O は `model_store`（読込）と batch ジョブ（`upsert_signals`）が持つ（ADR-016 の再現性規律＝§0.3 quant 共通型）。
- **前提テーブル**: `financials`（`0005_financials`・data-arch 定義／主キー `(code, disclosed_date, fiscal_period)`・列 `net_sales/operating_profit/profit/eps/bps`）／`signals`（`0003_signals`・quant 定義・**ai_alpha は新規 DDL なし**）／`index_quotes`（`0004`・TOPIX 終値＝超過リターンのベンチ）／`daily_quotes`（既存・`adj_close`）。
- **Phase 5 で新規の Alembic 移行は無い**（_arbitration 決定1 の通し番号表に `ai_alpha` 専用リビジョンは存在しない＝既存 `signals` 表を流用）。Phase 5 が触る DB スキーマはゼロ。

---

## 2. 特徴量設計（financials+将来リターン・特徴量リスト・リーク防止・データ量Free2年の制約）

quant.md §5.2 を確定。**特徴量は `financials` 由来＋`daily_quotes`（adj_close）由来**、ラベルは将来の対ベンチ超過リターン。

**特徴量リスト**（`backend/app/quant/ml/features.py` の純関数が組み立てる）:

| 特徴量 | 定義 | 出所 |
|---|---|---|
| 売上成長率 | YoY `net_sales` 変化率 | financials |
| 営業利益成長率 | YoY `operating_profit` 変化率 | financials |
| 純利益成長率 | YoY `profit` 変化率 | financials |
| 営業利益率 | `operating_profit / net_sales` | financials |
| EPS 成長率 | YoY `eps` 変化率 | financials |
| PER | `adj_close / eps`（開示日基準） | financials × daily_quotes |
| PBR | `adj_close / bps`（開示日基準） | financials × daily_quotes |
| サプライズ代理 | 開示日±数日の `adj_close` リターン | daily_quotes |
| モメンタム | 開示日時点の 3 か月 `adj_close` リターン | daily_quotes |

- 価格系列は全て **`adj_close`（調整後終値）** を使う（分割・併合の段差除去＝quant.md §0.1）。`adj_close` が窓内 null の銘柄・日は **skip**（補間しない＝数字を作らない・ADR-014・L-26）。
- **リーク防止（最重要）**: 特徴量は **開示日 `disclosed_date` 時点で既知の情報のみ**。ラベル（将来リターン）の期間と特徴量の期間が重ならないこと。**point-in-time** で組む（backtest 同様）。`features.py` は「ある開示行 ＋ その時点までの価格」だけを引数に取り、未来を覗かない設計にする。
- **ラベル（教師信号・既定）**: **決算開示後 60 営業日の対 TOPIX 超過リターン**（`(銘柄の60営業日後 adj_close リターン) − (TOPIX の同期間リターン)`）を **回帰**で予測（分類より情報量が多い＝quant.md §5.2）。`index_quotes` の TOPIX 終値をベンチに使う。**[OPEN] U-4**（§11）: 60日回帰が既定／代替は 20 日 or 2値分類。回帰/分類・期間を後から差し替えられる形（train.py 引数・`docs/ml-training.md` 記載値）で実装。
- **データ量の制約（Free 2 年）**: 財務 YoY を取るには複数期の `financials` が要る（Free は約 2 年分＝quant.md §5.2 [OPEN]）。学習データが薄い可能性があるため、**全銘柄 financials のバックフィルは別 PC 側で行ってよい**（ML 学習は別 PC＝ADR-006・data-arch §2.8）。ラズパイ側の `financials` は保有＋watchlist 限定でも推論はできる（推論は「その時点の特徴量 → score」なので YoY が組める銘柄のみ score を出し、組めない銘柄は skip）。学習データ量はモデルの質に直結するが、Free 2 年で足りるかは **学習着手時に別 PC で実測**（薄ければ Light で期間を延ばす・data-arch §1.9 `BACKFILL_YEARS`）。

---

## 3. 学習（別PC: train.py・予測ラベル=60日超過リターン回帰(既定)・評価・.pkl化・再現手順ドキュメント）

ADR-006: **学習は別 PC でのみ実行**（ラズパイでは走らせない）。

**配置**: `backend/app/quant/ml/train.py`（リポジトリには置くが、実行は別 PC・ラズパイの cron からは呼ばれない）。

**シグネチャ（純粋寄り・I/O は明示引数）**:
```python
# backend/app/quant/ml/train.py（ADR-006・ラズパイでは実行しない。docs/ml-training.md に再現手順）
def build_training_set(
    financials: pd.DataFrame,   # columns=[code, disclosed_date, fiscal_period, net_sales, operating_profit, profit, eps, bps]
    prices: pd.DataFrame,       # columns=[code, date, adj_close]（date 昇順）
    benchmark: pd.Series,       # TOPIX 終値（index=date）。対ベンチ超過リターンの計算用
    *,
    label_horizon_days: int = 60,   # 既定 60 営業日（U-4・後から差替可）
    label_kind: str = "regression",  # 'regression'(既定) | 'classification'
) -> tuple[pd.DataFrame, pd.Series, list[str]]:
    """point-in-time で特徴量 X・ラベル y・feature_names を組む（リーク防止＝§2）。純粋関数。"""

def train_model(
    X: pd.DataFrame, y: pd.Series, feature_names: list[str],
    *,
    label_kind: str = "regression",
    params: dict | None = None,   # LightGBM ハイパラ（num_leaves/learning_rate/n_estimators 等）
) -> tuple[object, dict]:
    """LightGBM を学習し (model, eval_metrics) を返す。回帰=LGBMRegressor / 分類=LGBMClassifier。"""

def save_model(
    model: object, feature_names: list[str], *,
    out_dir: str, kind: str = "ai_alpha", trained_at: str,  # 'YYYY-MM-DD'
    target: str, lib_version: str, notes: str = "",
) -> tuple[str, str]:
    """joblib で <kind>-<trained_at>.pkl を保存し、併置メタ JSON も書く（§4 のメタ規約）。戻り: (pkl_path, json_path)。"""
```

- **予測ラベル（既定）**: 60 営業日先の対 TOPIX 超過リターンを**回帰**。代替は 20 日 / 2 値分類（U-4・§11）。`label_kind`/`label_horizon_days` 引数で切替。
- **評価指標**（`docs/ml-training.md` に記録・quant.md §5.4）: 回帰 = **RMSE / IC（情報係数＝予測値と実現超過リターンの順位相関）**、分類 = AUC。**point-in-time の時系列 CV**（未来を学習に混ぜない・walk-forward 推奨）。
- **CV / ハイパラ**: LightGBM の `num_leaves` / `learning_rate` / `n_estimators` / `min_child_samples` を `docs/ml-training.md` に確定値で記録（再現性＝ADR-016）。
- **.pkl 化**: `joblib`（lightgbm/sklearn 標準・大配列に強い＝data-arch §5.3）。ファイル名は `ai_alpha-<YYYY-MM-DD>.pkl`、メタ JSON を併置（§4）。
- **再現手順ドキュメント（ADR-006 完了条件・必須）**: `docs/ml-training.md`（新規・**lead へ作成依頼**）に — ① 学習データ抽出 SQL（point-in-time）／② 特徴量・ラベル定義（§2 確定値）／③ `train.py` 実行コマンド・ハイパラ・CV 設定／④ 出力 `.pkl` の置き場とラズパイへの rsync 手順／⑤ 評価指標（RMSE/IC・AUC）。

---

## 4. 推論（ラズパイ: infer・joblibで.pkl読込・backend/models/(git管理外)・*-latest.jsonポインタ・バージョニング・batch相乗り）

data-arch §5.2-5.4・quant.md §5.1。

### 4.1 .pkl 置き場・配布（data-arch §5.2）
- **置き場**: `backend/models/`（`data/` と同じく **git 管理外**・compose で bind mount）。「再生成できる/別 PC 産の成果物」枠。
- **`.gitignore` 追記**: `backend/models/*.pkl`（＋メタ JSON も任意で除外）。
- **compose 変更**: `./backend/models:/app/models` を bind mount（named volume でも可）。
- **配布**: 別 PC で学習 → `.pkl`＋メタを**ラズパイの `backend/models/` に rsync で手動コピー**（ADR-006）。**イメージには焼かない**（モデル更新で再ビルドしない・ADR-021 はクロスビルドだがモデルは別管理）。自動化は YAGNI（data-arch §5.2 [OPEN]・推奨=手動 rsync）。

### 4.2 バージョニング・読込・メタ検証（data-arch §5.3）
```
backend/models/
  ai_alpha-2026-06-01.pkl     # モデル本体（別 PC 産）
  ai_alpha-2026-06-01.json    # メタ: {model_id, trained_at, feature_names[], lib_version, target, notes}
  ai_alpha-latest.json        # 現用モデルのポインタ {"active": "ai_alpha-2026-06-01"}
```
```python
# backend/app/ml/model_store.py（data-arch §5.3・DB は知らない・ファイル I/O のみ）
@dataclass
class ModelMeta:
    model_id: str
    trained_at: str            # 'YYYY-MM-DD'
    feature_names: list[str]
    lib_version: str           # lightgbm のバージョン（推論側と不一致なら警告/拒否）
    target: str                # 'excess_return_60d' 等
    notes: str

def load_active(kind: str = "ai_alpha") -> tuple[object, ModelMeta]:
    """*-latest.json の active を読み → 対応 .pkl を joblib.load → メタ検証。
    feature_names を推論時の入力列と照合（不一致なら ModelLoadError）。lib_version を実 lightgbm と照合（不一致は警告）。"""

class ModelLoadError(RuntimeError): ...
```
- シリアライズ = **joblib**（quant が pickle を選ぶなら合わせる・data-arch §5.3 [OPEN]）。
- バージョニング = **ファイル名に学習日** ＋ `*-latest.json` で現用を指す（**ロールバックは latest を旧日付に書き換えるだけ**）。
- 学習時と推論時の特徴量・前処理の不一致は**静かな事故**（ADR-018）。`feature_names` 照合と `lib_version` 照合でガードする。

### 4.3 推論ロジック（quant・純関数）
```python
# backend/app/quant/ml/infer.py（quant・DB を知らない・純関数）
def score_all(
    model: object,
    feature_names: list[str],
    feature_matrix: pd.DataFrame,   # index=code, columns=feature_names（point-in-time で as_of 日に組んだ特徴量）
    as_of: str,                     # 'YYYY-MM-DD' 推論基準日
    model_version: str,             # メタの model_id（payload に焼く・監査/再現用）
) -> list[dict]:
    """全銘柄スコアを signals 行候補 dict の配列で返す（DB は書かない）。
    1) model.predict(feature_matrix) で生の予測超過リターンを得る。
    2) 当日内で全銘柄をランク正規化（パーセンタイル）して score を 0..1 に。
    3) {date, code, signal_type:'ai_alpha', score, payload} を返す（payload は §5）。"""
```
- **score の正規化**: 予測超過リターンを **当日内で全銘柄ランク正規化（パーセンタイル）** して 0..1 に（quant.md §5.3）。生の予測値は payload に保持。
- 特徴量を組めない銘柄（YoY 不足・`adj_close` 欠損）は score を出さない（skip・§2）。

### 4.4 推論ジョブのバッチ相乗り（data-arch §5.1）
```python
# backend/app/batch/jobs/score_ai_alpha.py（NIGHTLY_JOBS に追加・data-arch 管轄の糊）
def run() -> JobResult:
    """1) model_store.load_active('ai_alpha')（無ければ skip＋通知・前日 signals は残る＝ADR-018）。
    2) financials/daily_quotes/index_quotes を repo から読み、quant.ml.features で point-in-time 特徴量を組む。
    3) quant.ml.infer.score_all(...) で signals 行候補を得る。
    4) repo.upsert_signals(rows)（signal_type='ai_alpha'・冪等 UPSERT）。
    5) 例外は JobResult(ok=False) で返し runner が Discord 通知（ADR-018）。"""
```
- **NIGHTLY_JOBS 配置**: 取得（fetch_quotes/financials）→ signals 計算（momentum/volume_spike）→ **score_ai_alpha** → run_advisor（AI が当日の ai_alpha を読めるよう AI ジョブの前）。順序は data-arch の `batch/jobs/__init__.py` で 1 か所定義。
- **推論失敗（モデル無し/メタ不一致）**: その夜の ai_alpha スコアリングを **skip して通知**（前日 signals は残る・ADR-018）。

---

## 5. signals書き込み（signal_type=ai_alpha・payload・既存DDL流用）

**既存 `signals` テーブル（`0003_signals`・quant.md §1.4）をそのまま使う。新テーブル・新リビジョンは作らない**（_arbitration 決定1）。`signal_type` 列に `'ai_alpha'` を入れるだけ。UNIQUE `(date, code, signal_type)` で冪等 UPSERT（既存 `repo.upsert_signals(rows)`・`index_elements=["date","code","signal_type"]`）。

**書き込み形**（quant.md §5.3）:
```json
{
  "date": "2025-12-15",
  "code": "72030",
  "signal_type": "ai_alpha",
  "score": 0.73,
  "payload": {
    "predicted_excess_return_60d": 0.082,
    "model_version": "ai_alpha_v1_20251201",
    "feature_snapshot": {"sales_growth_yoy": 0.12, "operating_margin": 0.09, "per": 14.2, "...": 0},
    "schema_version": 1
  }
}
```
- `score`（0..1）= 予測超過リターンを当日内でランク正規化（パーセンタイル）。生の予測値は `predicted_excess_return_60d` に保持。
- `model_version` を必ず残す（どのモデルが出したスコアか監査・再現用＝ADR-016）。
- `feature_snapshot` に推論時の主要特徴量を残す（AI/人間が「なぜ高スコアか」を辿れる）。
- `payload` は TEXT に JSON 文字列（SQLite に JSON 型なし・既存方針）。`schema_version`（int）を必ず入れる。
- **既存 signals 表の `label`/`change_5d`（B-6）の扱い**: momentum/volume_spike は payload に `label`/`change_5d` を持つ（quant.md §1.4）。ai_alpha も一覧 UI で人間可読にするため `label`（例「AI 決算スコア 上位」）を payload に入れてよい（必須ではないが UI 一貫性のため推奨）。

---

## 6. REST/Tool（get_signals(type=ai_alpha)・ランキング画面/API）

ai_alpha 専用の新 API は作らない。**Phase 1 で作る `GET /signals` と Tool `get_signals` の `type` フィルタで ai_alpha を取り出す**（ai-advisor.md §Tool 一覧・quant.md §1.5）。

### REST（app レーン・既存 `/signals` を流用）
- `GET /signals?type=ai_alpha&date=...&limit=...` … `repo.get_signals(conn, date, signal_type='ai_alpha', limit=...)`（**score 降順**＝ランキング）。`date` 省略時は `get_latest_signal_date('ai_alpha')` で最新算出日。`company_name` はルータが `signals JOIN stocks` で補完。
- 返却（既存 `/signals` と同形）: `{date, is_delayed, signals: [{code, company_name, signal_type, score, payload}]}`。

### Tool（ai-advisor レーン・既存 `get_signals` を流用）
```jsonc
get_signals(date?, type?, code?) ->
  {date, is_delayed, signals: [{code, company_name, signal_type, score, payload}]}
  // type="ai_alpha" でフィルタ。トップに date/is_delayed、行に company_name(JOIN)。
  // payload に predicted_excess_return_60d / model_version / feature_snapshot（§5）。
```
- `is_delayed`（Free 12 週遅延・ai-advisor.md §266）と `as_of`（鮮度日）はルータ/handler が付与（行レベル date は持たない）。
- AI は score の根拠に `model_version`/`feature_snapshot` を読めるが、**AI に再計算させない**（事実は Python 産＝ADR-014）。

---

## 7. frontend（スコアランキング画面・パス）

- **画面**: `frontend/src/app/signals/page.tsx`（Phase 1 で作る `/signals` 一覧に **ai_alpha ランキングのタブ/フィルタ**を足す＝専用ページを増やさず `signal_type` で切替）。AI 決算スコアの**降順ランキング**（code・company_name・score・主要 feature・predicted_excess_return）。
- **データ取得**: `frontend/src/lib/api.ts` に `getSignals(type?, date?, limit?)` を追加（ADR-005・DB に触れず REST 経由のみ）。型 `Signal`（`code`/`company_name?`/`signal_type`/`score`/`payload`）を backend Pydantic と対応させる。
- **遅延注記**: `is_delayed` が true のとき「約 3 か月前の決算/株価ベース」の注記を表示（Free 12 週遅延・data-model.md §3 の警告と同趣旨）。
- **スタイル**: Tailwind v4 トークン（`DESIGN.md`）・density-first・既存トークン（`surface-1`/`hairline`/`accent`/`num`）。score は 0..1 を UI で ×100 して % 表示（_arbitration 決定2）。
- コンポーネント冒頭コメントで `screens.md` 対応箇所と ADR を参照。
> Phase 1 着工時に `/signals` 画面の有無が確定するため、**ai_alpha タブは Phase 1 の一覧コンポーネントに追記**する形を推奨（app レーンと調整）。

---

## 8. 追加依存（lightgbm・ARMビルドゲート=推論側wheel確認）

- **`lightgbm`**（quant レーンが追加判断・quant.md §5.1）: **推論にも import が要る**ため backend 依存（`backend/pyproject.toml`）に入る。学習は別 PC だが、ラズパイ推論で `model.predict` を呼ぶのに lightgbm 本体が要る。
- **`joblib`**（data-arch が明示・§5.4）: `.pkl` の read/write。lightgbm に同梱されることが多いが本書で明示。
- **ARM ビルドゲート（最重要・実機確認・ユーザー裁定ではない）**: **lightgbm が aarch64（ラズパイ）で pip wheel が入るか**を、コードを書く前に **data-arch が Docker クロスビルドで検証**（ADR-021・責任分界＝依存追加判断は quant／クロスビルド段取りは data-arch・_arbitration 決定6）。
  - 入らない場合の最終手段（過剰なので最後）: **ONNX 等へ変換して推論だけ軽量化**（quant.md §5.1 [OPEN]）。まずは lightgbm aarch64 wheel を試す。
  - クロスビルドは別 PC でイメージ作成 → ラズパイは pull のみ（ADR-021・ADR-006 と同発想）。

---

## 9. テスト計画（推論の決定性・既知入力→既知スコア）

`backend/tests/`（pytest・既存 conftest 流儀・quant 関数は DB 不要な純関数テスト・data-arch §5.4）:

| テスト | 検証内容 | 出所 |
|---|---|---|
| 推論の決定性 | 同じ `.pkl`＋同じ特徴量 → 同じ score（バイト一致 or ±1e-9） | ADR-016・quant.md §5.5 |
| 特徴量組み立て | 既知の `financials` 行＋価格 → 期待する特徴量ベクトル（YoY・PER・PBR・モメンタム） | quant.md §5.5 |
| リーク検査 | ラベル期間と特徴量期間が重ならないことを assert する仕組み（point-in-time 違反を検出） | quant.md §5.2/§5.5 |
| score 正規化 | 既知の予測値分布 → 期待パーセンタイル（0..1） | quant.md §5.3/§5.5 |
| model_store 正常 | ダミー `.pkl`＋メタ JSON → `load_active` が model+meta を返す（実モデル不要） | data-arch §5.4 |
| model_store メタ不一致 | feature_names 不一致 → `ModelLoadError`、lib_version 不一致 → 警告 | data-arch §5.3/§5.4 |
| model_store 欠損 | `*-latest.json` or `.pkl` 欠損 → 推論ジョブが skip＋通知（前日 signals 残る） | data-arch §5.3・ADR-018 |
| signals 冪等 | 同日 ai_alpha を 2 回 `upsert_signals` → 重複しない（UNIQUE date,code,type） | quant.md §1.4・ADR-002 |
| score_ai_alpha ジョブ | quant 推論関数をスタブ → `upsert_signals` 行数・ok/失敗時通知（LLM/実モデル不要） | data-arch §5.4 |

- **train.py のテスト**は別 PC 前提のため軽量（特徴量・ラベル組立の純関数テストは CI で回せる。学習そのものは重く CI 対象外＝ADR-006）。

---

## 10. 着工順（チェックリスト）

**前提ゲート（着工前に潰す）**:
- [ ] **G1（実機確認）**: lightgbm aarch64 wheel が pip で入るか・Docker クロスビルドで backend イメージが aarch64 で通るか（data-arch 段取り・§8）。詰めば ONNX。
- [ ] **G2（前提テーブル）**: `financials`（`0005`）・`signals`（`0003`）・`index_quotes`（`0004`）が存在＝Phase 2 まで完了していること。
- [ ] **G3（U-4 確認）**: 予測ラベル＝60日超過リターン回帰（既定）で進めるか、ユーザーに確認（§11・後から差替可だが学習やり直しコスト中）。

**実装順（quant 純関数→学習→推論→配線→画面）**:
1. [ ] `quant/ml/features.py`（point-in-time 特徴量組立・純関数）＋テスト（特徴量組立・リーク検査）。
2. [ ] `quant/ml/train.py`（別 PC・`build_training_set`/`train_model`/`save_model`）＋特徴量/ラベル組立の純関数テスト。
3. [ ] `docs/ml-training.md`（再現手順・ADR-006 完了条件・lead 依頼）。別 PC で 1 回学習し `.pkl`＋メタを出力。
4. [ ] `app/ml/model_store.py`（`load_active`・メタ検証）＋テスト（正常/不一致/欠損）。`backend/models/` 作成・`.gitignore`・compose mount（data-arch）。
5. [ ] `quant/ml/infer.py`（`score_all`・ランク正規化）＋テスト（決定性・正規化）。
6. [ ] `batch/jobs/score_ai_alpha.py` を `NIGHTLY_JOBS`（run_advisor の前）に追加＋スタブテスト（data-arch）。
7. [ ] `repo.get_signals`/`upsert_signals` の type=ai_alpha 経路確認（既存・Phase 1 で実装済み前提）。
8. [ ] `GET /signals?type=ai_alpha`（app・既存 `/signals` の type フィルタ）＋ Tool `get_signals` の type 通し（ai-advisor）。
9. [ ] frontend `/signals` に ai_alpha ランキングタブ・`lib/api.ts` `getSignals` 追加。
10. [ ] 別 PC で学習した `.pkl` をラズパイへ rsync → 夜間バッチで実スコア算出 → 画面・Tool で確認（完了条件 1-3）。

> **着工順の肝**: 特徴量・推論の純関数とテスト（1・2・5）を先に固め、`.pkl` 読込・バッチ・API・画面は後（計算の真実＝ADR-016 を先に確定）。学習は別 PC で並行（ADR-006）。

---

## 11. このPhaseの[OPEN]（U-4 MLラベル・既定60日回帰／_open-questions.md参照）

### ユーザー裁定が要る（`_open-questions.md` U-4・推奨値を spec の既定に採用済み）
- **U-4: P5 ML の予測ラベル** — **既定＝60 日先の対 TOPIX 超過リターンを回帰**。代替＝20 日 / 2 値分類（上昇 or not）。影響 Phase 5・後から変える容易さ「中（特徴量・学習やり直し）」。`train.py` の `label_kind`/`label_horizon_days` 引数と `docs/ml-training.md` の記載値で差替可。投資の好み（短期サプライズ反応 vs 中期ファンダ）に関わるため**学習着手前にユーザー確認**を推奨。

### 技術リスク（実機確認・ユーザー裁定ではない）
- **lightgbm の aarch64 ビルド**（§8・ADR-021）: pip wheel が入るか・クロスビルドで通るか。詰めば ONNX 変換（最終手段）。Phase 5 着手の最初のゲート（data-arch 段取り・quant 依存選定）。
- **Free 2 年のデータ量**（§2）: 財務 YoY を組むのに複数期が要り学習データが薄い恐れ。全銘柄 financials バックフィルは別 PC 側で可（ADR-006）。薄ければ Light で期間延長。学習着手時に別 PC で実測。

### 他レーン依存・要確認
- **data-arch**: `financials`(`0005`) の取得範囲を P5 は全銘柄へ拡張（学習は別 PC バックフィルでも可・data-arch §2.8）。`backend/models/` の bind mount・`.gitignore`・推論ジョブの `NIGHTLY_JOBS` 配置。
- **quant**: 特徴量・ラベル・推論ロジック・正規化（本書 §2-5・§9 が正本）。
- **ai-advisor**: `get_signals(type=ai_alpha)` を Tool で使う（返却は既存 `get_signals` 形・ai-advisor.md §Tool 一覧）。
- **app**: `GET /signals?type=ai_alpha`（既存 `/signals` の type フィルタ）・`/signals` ランキング画面。
- **lead**: `docs/ml-training.md`（新規・ADR-006 完了条件）の作成。
