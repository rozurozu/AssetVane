# AI Alpha Scorer 学習の再現手順（別 PC・ADR-006）

> **状態（2026-06-30・初回学習実測ずみ）**: 推論側コード（`features.py`/`infer.py`/`model_store.py`/
> `batch/jobs/score_ai_alpha.py`）・学習コード（`quant/ml/train.py`＝`walk_forward_cv` 追加）・学習 CLI
> （`app/scripts/train_ai_alpha.py`）・ベンチ投入（`backfill_topix_benchmark.py`）・`make backfill-topix`/
> `make train-ai-alpha`（ADR-066）まで実装済み。**2026-06-30 に開発機 Mac のコンテナ内で初回学習を
> 完走**し、本書の `【実測】`（データ量・ハイパラ・CV の RMSE/IC）を穴埋めずみ（完了条件 4 達成）。
> 学習済み `.pkl`（`ai_alpha-2026-06-30`）は `backend/models/` に配置。本番ラズパイへは rsync 配布が残。

Phase 5 の AI Alpha Scorer は、決算ファンダ＋将来リターンで学習した LightGBM のスコアを Advisor の
材料（事実）に加える。**学習は別 PC でのみ実行**（ADR-006）。ラズパイは `.pkl` を読んで推論するだけ。

---

## 0. 役割分担（ADR-006）

```
[別 PC（高負荷・低頻度）]  financials + daily_quotes + index_quotes(TOPIX)
   → quant/ml/train.py（build_training_set → train_model → save_model）
   → ai_alpha-<YYYY-MM-DD>.pkl + メタ JSON + ai_alpha-latest.json
   → rsync で ラズパイの backend/models/ へ

[ラズパイ（軽量・毎晩）]  backend/app/ml/model_store.load_active("ai_alpha")
   → quant/ml/infer.score_all → signals(ai_alpha) へ UPSERT（score_ai_alpha ジョブ）
```

- `quant/ml/train.py` はリポジトリに置くが、**`NIGHTLY_JOBS` には含めない**（ラズパイで学習しない）。
- 学習データの財務バックフィルは別 PC 側で行ってよい（ラズパイの financials は保有＋watchlist 限定でも
  推論は成立する＝§推論）。**Free は約 2 年分**なので YoY を取ると学習データが薄い恐れ。薄ければ
  J-Quants **Light 以上**で期間を延ばす（`BACKFILL_YEARS` 相当を別 PC で広げる）。
  - **【実測】データ量（2026-06-30・開発機 Mac のコンテナ内・Free プラン）**: `financials` 31,042 行・
    `daily_quotes` 1,966,335 行・ベンチ（`1306.T`）1,096 行（2022-01-04〜2026-06-30）。point-in-time で
    組めた**学習サンプル 22,219 件**（horizon=60）。財務は Free の前線 2026-03 までだが、YoY の前年同期は
    2 年分あるため NaN 率は低い（売上/利益 YoY ≈ 3%・PER/PBR ≈ 6%）。

---

## 1. 学習データの抽出（point-in-time）

別 PC 上の DB（または別 PC へコピーした `assetvane.db`）から 3 系統を読む。`build_training_set` は
**point-in-time**（リーク防止）で特徴量とラベルを組むので、抽出は「全期間の素データ」をそのまま渡せばよい
（未来の切り出しは関数側が担保）。

```sql
-- 財務（全銘柄・全期間）。fiscal_period は J-Quants CurPerType（'FY'/'1Q'/'2Q'/'3Q'）。
SELECT code, disclosed_date, fiscal_period, net_sales, operating_profit, profit, eps, bps
FROM financials ORDER BY code, disclosed_date;

-- 日足（調整後終値）。分割の段差を除くため adj_close を使う。
SELECT code, date, adj_close FROM daily_quotes ORDER BY code, date;

-- ベンチ＝TOPIX 終値（index_quotes の symbol='^TPX'）。
SELECT date, close FROM index_quotes WHERE symbol='^TPX' ORDER BY date;
```

> **⚠ ベンチの調達（Free プラン・2026-06-30 実測で判明）**: TOPIX 指数（`^TPX`）は **J-Quants Light
> 以上**でしか取れず（Free=403）、Yahoo/Stooq にも有効な TOPIX 指数シンボルが無い（`adapters/index.py`
> の注記）。そのため Free では **TOPIX 連動 ETF `1306.T`（NEXT FUNDS TOPIX ETF）の配当調整後 close を
> ベンチのプロキシ**に使う。`make backfill-topix`（= `app.scripts.backfill_topix_benchmark`・IndexAdapter
> の Yahoo 恒等取得）で `index_quotes` に `symbol='1306.T'` として投入し、学習は
> `make train-ai-alpha ARGS="--bench-symbol 1306.T"` で参照する。総リターン連動なので相対超過リターンの
> ベンチとして妥当。Light 以上にしたら本物の `^TPX` を入れて `--bench-symbol ^TPX`（既定）に戻せばよい。

---

## 2. 特徴量・ラベルの定義（確定）

**特徴量**（`quant/ml/features.py` の `FEATURE_NAMES`・順序固定・学習/推論で共有）:

| 特徴量 | 定義 |
|---|---|
| `sales_growth_yoy` | 売上 YoY（同一 fiscal_period タイプの直前行＝前年同期と突合） |
| `operating_profit_growth_yoy` | 営業利益 YoY（同上） |
| `profit_growth_yoy` | 純利益 YoY（同上） |
| `operating_margin` | 営業利益 / 売上（最新開示行） |
| `eps_growth_yoy` | EPS YoY（**FY 行基準**＝四半期 EPS の累計を避ける） |
| `per` | as_of 終値 / 最新FY EPS |
| `pbr` | as_of 終値 / 最新FY BPS |
| `surprise_proxy` | 開示日近傍（±3 営業日）の株価リターン |
| `momentum_3m` | as_of / 60 営業日前 の株価リターン |

- YoY は **同一 `fiscal_period` タイプの直前行**と突合（FY↔前FY・四半期↔前年同四半期）。`fiscal_period`
  は年を含まないため、同タイプで `disclosed_date` が直前の行をそのまま前年同期として使う。
- PER/PBR・EPS 成長率は**通期(FY)行**を使う（既存 `quant/valuation.py` の採用規律に揃える）。
- 欠損は NaN のまま（LightGBM が欠損として扱う）。捏造しない（ADR-014）。

**ラベル（既定・U-4 裁定済み）**: 決算開示後 **60 営業日**の対 TOPIX 超過リターンを**回帰**で予測。
```
y = (銘柄 adj_close[t+60]/adj_close[t] - 1) - (TOPIX[t+60]/TOPIX[t] - 1)
   t = 開示日の翌営業日（観測→翌日エントリ。特徴量窓<=開示日 と非重複＝リーク防止）
```
- 差替: `build_training_set(..., label_horizon_days=20, label_kind="classification")` 等（**分類化は
  しない方針**だが分岐は残す）。差替時は本書の値も更新する。

---

## 3. 学習の実行（別 PC＝開発機のコンテナ内・ADR-066）

**正準フロー（推奨・現用 DB を読み取り専用で直読＝バックアップ吸い出し不要）**:

```bash
# 1) ベンチ（Free は TOPIX 指数が取れないため TOPIX ETF プロキシを投入。§1 の ⚠ 参照）
make backfill-topix                                  # 既定 1306.T・2022-01-01 以降
# 2) 学習（CV → fit → .pkl/メタを backend/models/ へ・引数で horizon/bench-symbol を変えられる）
make train-ai-alpha ARGS="--bench-symbol 1306.T"
```

`make train-ai-alpha` は `docker compose run --rm --no-deps backend uv run python -m
app.scripts.train_ai_alpha` で、現用 DB（named volume `assetvane-db`）を `?mode=ro` で読む（ADR-002 の
書きロック競合なし）。`.pkl` は `./models`（bind mount でホスト `backend/models/`）に出る。本番（ラズパイ・
推論のみ）へは `.pkl` を rsync 配布（§4）。**学習は別 PC のみ**（ADR-006）＝`NIGHTLY_JOBS` に学習は無い。

`train_ai_alpha.py`（CLI）の中身は下記とほぼ同じ。別 DB ファイルを直に渡したいときは
`make train-ai-alpha ARGS="--db /path/to/assetvane.db --bench-symbol 1306.T"`、または手書きで:

```python
# train_ai_alpha.py（別 PC で実行・ADR-006。リポジトリの backend/ を PYTHONPATH に通す）
import lightgbm, pandas as pd, sqlite3
from app.quant.ml.train import build_training_set, train_model, save_model

con = sqlite3.connect("assetvane.db")
fin = pd.read_sql("SELECT code,disclosed_date,fiscal_period,net_sales,operating_profit,profit,eps,bps FROM financials", con)
px  = pd.read_sql("SELECT code,date,adj_close FROM daily_quotes", con)
bench = pd.read_sql("SELECT date,close FROM index_quotes WHERE symbol='^TPX'", con).set_index("date")["close"]

X, y, names = build_training_set(fin, px, bench, label_horizon_days=60)
model, metrics = train_model(X, y, names)          # params= で確定ハイパラを渡す
print(metrics)                                      # RMSE / IC を docs に記録
save_model(model, names, out_dir="models",
           trained_at="2026-06-15",                 # 学習日
           target="excess_return_60d",
           lib_version=lightgbm.__version__,
           notes="Free 2y / 60d excess vs TOPIX")
```

### ハイパラ（`【実測】`）
`train.py` の `_DEFAULT_PARAMS`: `n_estimators=300 / learning_rate=0.03 / num_leaves=31 /
min_child_samples=20 / subsample=0.8 / colsample_bytree=0.8 / random_state=42`。
- **【実測】（2026-06-30 初回）**: 上記 `_DEFAULT_PARAMS` をそのまま採用（グリッド探索による調律は未実施）。
  `random_state=42` 固定で再現性を担保（ADR-016）。次の改善余地＝walk-forward CV を回しながらの
  `num_leaves`/`min_child_samples`/`learning_rate` の調律（今は出発値のまま）。
- `walk_forward_cv`（`train.py`・expanding window・時系列順・リーク無し）で fold ごとに過去学習→未来評価。

### 評価指標（`【実測】`）
- 回帰: **RMSE** と **IC（情報係数＝予測と実現超過リターンの spearman 順位相関）**。
- **【実測】（2026-06-30・サンプル 22,219・horizon=60・ベンチ 1306.T・splits=5）**:
  **walk-forward CV** = RMSE **0.2316 ± 0.0399** / IC **0.0814 ± 0.0673**（out-of-sample）。
  参考の in-sample（全データ fit）= RMSE 0.1924 / IC 0.4211（過学習ギャップは想定内＝CV が正直な値）。
  決算ファンダのクロスセクション予測として IC ≈ 0.08 は妥当な水準。
- （分類にした場合のみ）AUC。

---

## 4. 出力の配置とラズパイへの配布

`save_model` は `out_dir` に 3 ファイルを書く:

```
ai_alpha-2026-06-15.pkl     # モデル本体（joblib）
ai_alpha-2026-06-15.json    # メタ {model_id, trained_at, feature_names[], lib_version, target, notes}
ai_alpha-latest.json        # 現用ポインタ {"active": "ai_alpha-2026-06-15"}
```

ラズパイの `backend/models/`（**git 管理外**・bind mount）へ rsync する:

```bash
rsync -av models/ai_alpha-2026-06-15.pkl models/ai_alpha-2026-06-15.json models/ai_alpha-latest.json \
  deploy@assetvane-pi:/opt/assetvane/backend/models/
```

- **イメージには焼かない**（モデル更新で再ビルドしない・ADR-021/006）。`.pkl` は別管理。
- 次の夜間バッチで `score_ai_alpha` ジョブが `load_active("ai_alpha")` → 推論 → `signals(ai_alpha)` を焼く。
- **lib_version 整合**: 学習機と推論機（ラズパイ）の lightgbm は同系統に保つ。メタの `lib_version` と
  推論側 `lightgbm.__version__` が食い違うと `model_store` が警告する（拒否はしない）。

### ロールバック
旧モデルに戻すには `ai_alpha-latest.json` の `active` を旧 stem に書き換えるだけ（`.pkl` は残っている前提）。

---

## 5. 注意（ADR）

- **学習はラズパイで走らせない**（ADR-006）。`train.py` は別 PC 専用。
- **推論の決定性**（同じ `.pkl`＋同じ特徴量→同じ score）はテストで担保（`test_quant_ml_infer.py`）。
- **特徴量・前処理の不一致は静かな事故**（ADR-018）。`feature_names` 照合（不一致は `ModelLoadError`）で防ぐ。
- `.pkl` は**利用者本人が別 PC で作る信頼できる成果物**（ADR-001 単一ユーザー）。外部由来の pickle は読まない。
