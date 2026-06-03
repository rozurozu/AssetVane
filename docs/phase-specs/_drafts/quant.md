# quant レーン仕様（シグナル計算・数理最適化・ML）— 全 Phase

> 担当: quant（シグナル・最適化・ML）。**コードは書かず、着工可能な仕様だけを書く**。
> 参照: [ADR-014/016](../../decisions.md)（手法はコード・AI は計算しない）・[ADR-006](../../decisions.md)（学習は別 PC）・[ADR-008](../../decisions.md)（12週間遅延）・[ADR-021](../../decisions.md)（ARM ビルド）・[data-model.md §4](../../data-model.md)（signals）・[roadmap.md P1/P2/P5/P7](../../roadmap.md)・[advisor.md §4/§5](../../advisor.md)（Tool 一覧の数値はこのレーンが供給）。
> 接地: [_current-state.md](_current-state.md)。**現状、数理/ML 依存はゼロ**（pandas すら未導入）。DB は `stocks`/`daily_quotes` の 2 表のみ。`daily_quotes` は `open/high/low/close/volume/adj_close`（全 Float・nullable）・複合 PK `(code,date)`・`date` は `'YYYY-MM-DD'` 文字列。
>
> **R3 改訂（2026-06-03）**: [_arbitration.md](_arbitration.md)（正本）と [_review.md](_review.md) D節(quant) に機械的に揃えた。反映: B-1（リビジョン `0003_signals`）・B-2（Tool 返却を正本スキーマへ完全一致）・B-3（比率は全て 0..1）・B-6（payload に `label`/`change_5d` を quant が格納）・B-12（deviations は単一関数で2出力先へ供給）・B-10（依存追加判断=quant／クロスビルド段取り=data-arch の分界に同意）。

---

## 単位の約束（B-3・[_arbitration.md 決定2]）

**比率・weight・cash_ratio・cash_weight・correlation のしきい・deviation の current/limit は、本書・DB・API・Tool すべて 0..1 で表す**（UI でのみ ×100 して %）。本書の数値例は全て 0..1（例 `0.18`＝18%）。`score` も 0.0〜1.0。

---

## 0. 全 Phase 共通の確定方針（最初に読む）

### 0.1 価格系列は `adj_close`（調整後終値）を使う — ただし OHLV は未調整

[_current-state.md §5] の通り、adapter は **`adj_close`（V2 `AdjC`）だけを保存**し、`AdjFactor`・調整後 OHLV（`AdjO/AdjH/AdjL/AdjVo`）は保存していない。`open/high/low/close/volume` は**未調整**。

確定ルール（全手法共通）:

- **トレンド・モメンタム・リターン・相関・最適化・ML のリターン特徴量** … すべて **`adj_close`** から計算する（分割・併合の段差を除去するため。未調整 `close` で SMA/RSI を計算すると分割日に偽シグナルが出る）。
- **出来高（`volume`）** … 未調整のまま使う。volume_spike は**比率（当日 ÷ 平均）**で見るため、調整係数が一定区間内で揃っていれば比率は概ね保たれる。ただし分割直後は出来高も段差が出るので、後述の通り spike 判定窓に分割をまたぐ期間は**スコアに `adj_warning` フラグ**を立てる（誤検知の自衛）。
- **`high`/`low`（RSI 等の一部で素の値が要る指標）** … 現状未調整しか無い。RSI は **`adj_close` の差分**で計算する（Wilder の RSI は終値ベースが標準なので high/low 不要）。**high/low を直接使う指標（ATR・ストキャスティクス等）は P1 では実装しない**（未調整 high/low と調整 close の混在を避ける）。導入するなら adapter に調整 OHLV 列を足してから（**[DOCS要修正]** 候補・§9）。

**adj_close の欠損方針（確定・L-26）**: `adj_close` が null の行（上場直後・データ欠損）は、**計算窓内に null があればその銘柄・その日のシグナルを生成しない（skip）**。前方補完・線形補間はしない（価格の捏造になる＝ADR-014「数字を作らない」規律）。[_arbitration.md 決定7 L-26] で lead 確定。

### 0.2 計算は pandas（時系列）で行う。指標ライブラリは自前実装を基盤にする（§1 で確定）

### 0.3 再現性（ADR-016）の担保方法 — 全手法共通の型

各手法は「**入力 DataFrame → 出力 dict/DataFrame**」の**純粋関数**として実装する（DB I/O を関数内に持たない）。これにより:

- **テスト**: 既知の系列（手で作った OHLCV）→ 既知のシグナル値を assert（後述の各テストケース）。
- **backtest**: 過去の任意日を「その日が最新」として関数に食わせれば再計算できる（決定的）。
- **事前計算**: 夜間バッチが全銘柄ループで関数を呼び `signals` に焼く（[data-arch] のバッチが呼び出し側）。

関数は `backend/app/quant/` 配下に手法ごとのモジュールで置く（新規ディレクトリ）。バッチ（`app/batch/`・[data-arch] 管轄）から呼ばれる。**quant モジュールは DB を知らない**（dict を返すだけ。`signals` への書き込みは呼び出し側）。

### 0.4 12 週間遅延でのロジック検証方針（ADR-008）

遅延は「データの鮮度」の問題で「計算の正しさ」とは独立（ADR-008）。検証は 2 段で行う:

1. **ユニットテスト（鮮度非依存）**: 手で組んだ既知系列で関数の出力を固定値と突き合わせる。これがロジックの一次担保。日付が 12 週間前でも計算結果は同じ。
2. **遅延データでの妥当性確認**: Free の約 3 か月前データでバッチを回し、`signals` に出た銘柄を**チャートで目視**（SMA クロスが実際にその日付で起きているか・出来高棒が突出しているか）。Light 以上で最新化しても**ロジックは不変**（パラメータ・式が同じ）。
3. backtest（P2・§6）は「その日を最新と見なして再計算」なので、遅延データでも**過去日に対して**正しく検証できる。遅延は backtest の対象期間が古いだけで、検証の質は落ちない。

---

## Phase 1: Trend Vane（momentum / volume_spike）

[roadmap.md P1] / [advisor.md §4]（`get_signals`・`get_indicators`・`screen_stocks`）。

### 1.1 指標ライブラリ選定 — **自前実装（numpy/pandas）を確定推奨**

| 候補 | ARM/Docker ビルド | numpy 2 互換 | 保守状況 | 評価 |
|---|---|---|---|---|
| **TA-Lib（C 拡張）** | ✕ C 本体の OS パッケージ要・ARM ビルド難（[ADR-021] が明示） | 要ラッパ確認 | 本体は枯れているが導入が重い | **不採用**（ARM コンテナでの導入コストが高い） |
| **pandas-ta（純 Python）** | ○ pure python | △ **本家は numpy2 非対応、fork（pandas-ta-openbb）頼み** | ⚠ **2026-07-01 までに追加支援が無ければアーカイブ予定**（公式告知）| **不採用**（保守リスク・依存の将来不確実） |
| **自前実装（numpy/pandas のみ）** | ◎ 追加 C 依存なし | ◎ numpy/pandas に追従するだけ | ◎ 自分で保守・テスト可 | **★推奨** |

**推奨: 自前実装。** 理由:

- P1 で必要な指標は **SMA・RSI（Wilder）・出来高移動平均の 3 つだけ**。いずれも 10〜20 行で正確に書け、C 依存も外部保守リスクも持ち込まない。
- ADR-016 が要求する「**テスト済みコードで実装**」と完全に整合（自前なら既知系列テストを自分で持てる）。むしろ外部ライブラリだと「ライブラリの実装＝真実」になりテスト責務が曖昧になる。
- ADR-021 の ARM ビルド難（TA-Lib）・pandas-ta のアーカイブリスクを両方回避。
- 将来 ATR/ボリンジャー等が増えても自前モジュールに足せる。本当に大量化したら**その時** TA-Lib 再評価（クロスビルド前提・ADR-021）。

**追加依存（backend `pyproject.toml`）**:
```
numpy>=2.0
pandas>=2.2
```
（`scipy` は P1 不要。P2 の相関・最適化で追加）。numpy/pandas のラズパイ ARM wheel は manylinux aarch64 で配布済みのため pip で入る見込みだが、Docker クロスビルド（ADR-021）時に実機確認すること。

> **責任分界（B-10・[_arbitration.md 決定6] に同意）**: **依存の追加判断＝quant**（どのライブラリをどのバージョンで入れるか）、**Docker クロスビルド検証の段取り＝data-arch**（別 PC でのイメージ作成と aarch64 での起動確認）。numpy/pandas を入れたイメージが aarch64 で通るかは **Phase 1 着手の最初のゲート**。

### 1.2 momentum シグナル

**関数シグネチャ**（`backend/app/quant/momentum.py`）:
```python
def compute_momentum(quotes: pd.DataFrame) -> dict | None:
    """1 銘柄の日足から momentum シグナルを 1 件算出（最新日基準）。
    quotes: columns=[date, adj_close]（date 昇順・adj_close は調整後終値）。
    戻り値: signals 行の payload 候補 dict、シグナル不成立/データ不足なら None。
    （ADR-016: 純粋関数・DB を知らない。docs/data-model.md §4）
    """
```

**確定パラメータと数式**（すべて `adj_close` で計算）:

| 要素 | 確定値 | 数式・定義 | 理由 |
|---|---|---|---|
| 短期 SMA | **25 日** | `sma25 = adj_close.rolling(25).mean()` | 日本株の標準的な短期線（約 5 週） |
| 長期 SMA | **75 日** | `sma75 = adj_close.rolling(75).mean()` | 標準的な中期線（約 15 週）。25/75 は日本株チャートの定番組 |
| ゴールデンクロス | 当日 `sma25 > sma75` かつ前日 `sma25 <= sma75` | `gc = (sma25.shift(1) <= sma75.shift(1)) & (sma25 > sma75)` | 「上抜けした瞬間」を捉える |
| RSI | **Wilder RSI(14)** | 下記 1.2.1 | テクニカルの世界標準。期間 14 は Wilder 原典 |
| RSI 反転（買い） | RSI が前日 **< 30**（売られすぎ）から当日 **>= 30** へ回復 | `rsi_rev = (rsi.shift(1) < 30) & (rsi >= 30)` | 売られすぎからの反転を 1 点で捉える |
| 最低データ長 | **76 行以上**（75 日 SMA ＋クロス判定に前日が要る） | 不足なら `None` | 計算窓が満たない銘柄を除外 |

**スコア定義（`score` 列, 0.0〜1.0）**: 2 つのサブシグナルの合成。
```
score = 0.6 * gc_today + 0.4 * rsi_rev_today
```
- `gc_today`/`rsi_rev_today` は当日 True=1.0/False=0.0。
- **両方成立で 1.0、GC のみ 0.6、RSI 反転のみ 0.4、どちらも無ければシグナル不成立（None を返す＝行を作らない）**。
- 重み（GC=0.6 > RSI=0.4）の理由: トレンド転換（GC）の方がモメンタムの主因として強く、RSI 反転は補助。**[OPEN]** この重み配分はユーザーの好み次第。推奨は 0.6/0.4。等加重（0.5/0.5）や GC のみ採用も選べる。

#### 1.2.1 Wilder RSI(14) の確定式（自前実装の真実）

```
delta   = adj_close.diff()
gain    = delta.clip(lower=0)
loss    = (-delta).clip(lower=0)
# Wilder の平滑化（EMA alpha=1/period ではなく Wilder smoothing）
avg_gain = gain.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
avg_loss = loss.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
rs  = avg_gain / avg_loss
rsi = 100 - 100 / (1 + rs)
# avg_loss==0 のとき rsi=100 にする（ゼロ割回避）
```
> **注意**: 単純移動平均版 RSI ではなく **Wilder 平滑（`ewm(alpha=1/14, adjust=False)`）** を採用する。これが TA-Lib・各証券会社チャートの既定と一致し、再現性・検証可能性が高い。テストケースもこの定義で固定する。

**signals 書き込み形**（`signal_type='momentum'`）:
```json
{
  "date": "2025-12-15",
  "code": "72030",
  "signal_type": "momentum",
  "score": 0.6,
  "payload": {
    "golden_cross": true,
    "rsi_reversal": false,
    "sma25": 2850.4,
    "sma75": 2790.1,
    "rsi14": 41.2,
    "adj_close": 2901.0,
    "label": "SMA25/75 ゴールデンクロス",
    "change_5d": 0.034,
    "schema_version": 1
  }
}
```

### 1.3 volume_spike シグナル

**関数シグネチャ**（`backend/app/quant/volume_spike.py`）:
```python
def compute_volume_spike(quotes: pd.DataFrame) -> dict | None:
    """1 銘柄の日足から出来高急増シグナルを 1 件算出（最新日基準）。
    quotes: columns=[date, volume, adj_close]（date 昇順）。
    戻り値: signals 行の payload 候補 dict、不成立/データ不足なら None。
    """
```

**確定パラメータと数式**:

| 要素 | 確定値 | 定義 | 理由 |
|---|---|---|---|
| 基準平均 | **過去 20 営業日の volume 単純平均（当日除く）** | `vol_ma20 = volume.shift(1).rolling(20).mean()` | 約 1 か月の平常出来高。当日を含めると spike が薄まるので除外 |
| spike 比率 | `ratio = volume / vol_ma20` | — | 当日出来高が平常の何倍か |
| spike 閾値 | **ratio >= 3.0** | `is_spike = ratio >= 3.0` | 平常の 3 倍を「急増」とする（日本株で広く使われる目安） |
| 出来高フロア | **vol_ma20 >= 50,000 株** | 流動性が極端に低い銘柄を除外 | 平常出来高が小さい銘柄は比率が暴れて誤検知するため |
| 最低データ長 | **21 行以上** | 不足なら `None` | 20 日平均＋当日 |
| 分割警告 | 計算窓 20 日内で `adj_close` の段差比が大きい場合 `adj_warning=true`（スコアは出すが要注意フラグ）| §0.1 | 未調整 volume の段差誤検知の自衛 |

**スコア定義（`score`, 0.0〜1.0 にクリップ）**:
```
score = min(ratio / 10.0, 1.0)   # ratio=3 → 0.3、ratio>=10 → 1.0
```
- 閾値 3.0 未満は `None`（行を作らない）。**[OPEN]** スケール基準（÷10 で 10 倍を上限 1.0）はユーザーの感覚次第。推奨は ÷10。閾値そのもの（3.0 倍）も 2.0/2.5 へ緩める選択あり。推奨 3.0。

**signals 書き込み形**（`signal_type='volume_spike'`）:
```json
{
  "date": "2025-12-15",
  "code": "68570",
  "signal_type": "volume_spike",
  "score": 0.42,
  "payload": {
    "volume": 4200000.0,
    "vol_ma20": 1000000.0,
    "ratio": 4.2,
    "adj_warning": false,
    "label": "出来高 平常の4.2倍",
    "change_5d": -0.012,
    "schema_version": 1
  }
}
```

### 1.4 `signals` 最終 DDL（[data-model.md §4] を確定）

[data-model.md §4] の案を実装可能な DDL に確定する。**`metadata` は既存（schema.py）に追加**（ADR-005・スキーマは Python 一元管理）。autogenerate マイグレーション **`0003_signals`（down_revision=`0002_fetch_meta`）**（[_arbitration.md 決定1] の通し番号表。`fetch_meta`(`0002`・data-arch)の後に `signals` が乗る単線チェーン。**移行ファイルの発行は data-arch が一元管理**し、`signals` の**定義内容の正本は quant**）。

```python
# backend/app/db/schema.py に追記（data-model.md §4・ADR-002）
signals = Table(
    "signals",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("date", String, nullable=False),          # 算出日 'YYYY-MM-DD'
    Column("code", String, nullable=False),          # 銘柄/業種コード（5桁）
    Column("signal_type", String, nullable=False),   # 'momentum'|'volume_spike'|'ai_alpha'|'lead_lag'
    Column("score", Float, nullable=False),          # 0.0〜1.0 のスコア・強度
    Column("payload", String),                       # JSON 文字列（指標値・根拠）
    UniqueConstraint("date", "code", "signal_type", name="uq_signals_date_code_type"),
    Index("ix_signals_date_type", "date", "signal_type"),
    Index("ix_signals_code", "code"),
)
```

**確定事項**:
- **冪等性（ADR-002）**: `(date, code, signal_type)` に **UNIQUE 制約**を張り、書き込みは UPSERT（`on_conflict_do_update`、既存 `repo._upsert` と同型）。同じ夜のバッチ再実行で重複しない。**[DOCS要修正]**: data-model.md は PK が `id` のみでこの UNIQUE に言及がない。冪等のため UNIQUE を追記すべき（→ [adr-guardian]/[lead] へ）。
- **`payload` は TEXT に JSON 文字列**（SQLite は JSON 型なし。既存方針と同じく Python 側で `json.dumps`/`json.loads`）。各 payload に `schema_version`（int）を必ず入れ、将来のスキーマ変更に備える。
- **`label`（短文）・`change_5d` は quant が payload に格納する（B-6・[_arbitration.md 決定2]）**。`label` は一覧の人間可読な短文（例「SMA25/75 ゴールデンクロス」「出来高 平常の4.2倍」）、`change_5d` は直近 5 営業日の `adj_close` 変化率（0..1 ではなく**騰落率**＝符号付き小数。例 +3.4%→`0.034`）。**`company_name` は持たない**（[app] ルータが `signals JOIN stocks` で補完する＝行レベルに名前を焼かない）。
- **`code`** は `daily_quotes.code` と同じ 5 桁内部コード（業種シグナル＝lead_lag では業種コードが入りうるので FK 制約は張らない）。
- **インデックス**: `(date, signal_type)`（一覧・通知の主クエリ）＋ `code`（銘柄詳細での横断取得）。

### 1.5 repo 関数（[app]/[data-arch] と契約）

`backend/app/db/repo.py` に追加（戻り値は素の dict・ADR-005）:
```python
upsert_signals(rows: list[dict]) -> int                      # index_elements=["date","code","signal_type"]
get_signals(conn, date: str | None, signal_type: str | None,
            code: str | None = None, limit: int = 100) -> list[dict]   # date 降順・score 降順
get_latest_signal_date(conn, signal_type: str | None = None) -> str | None
```
- `get_signals` は `date` 省略時「最新算出日」を自動採用（`get_latest_signal_date`）。これが Tool `get_signals` と REST `/signals` の供給源。

### 1.6 Tool 契約（[ai-advisor] と突き合わせ必須・[_arbitration.md 決定2] が正本）

このレーンが**実体（Python 計算）を供給**する Tool。**返却スキーマは [_arbitration.md 決定2] の確定スキーマに完全一致させた**。原則: quant 純関数が「事実」を計算 → Tool handler(ai-advisor) が薄く包む → app の REST 型と一致。遅延フラグは `is_delayed: bool`、鮮度日は `as_of: "YYYY-MM-DD"`、比率は 0..1。

**`get_indicators(code)`** — **平坦**（sma5 は P1 では計算しない＝ネスト `sma:{}` は採用しない）:
```jsonc
{
  "code": "72030",
  "as_of": "2025-12-15",
  "adj_close": 2901.0,
  "sma25": 2850.4,
  "sma75": 2790.1,
  "rsi14": 41.2,
  "vol_ma20": 1000000.0,
  "is_delayed": true
}
```
- 「シグナルの有無」ではなく「**最新日(`as_of`)の指標値そのもの**」を返す（AI が「RSI は今いくつ？」に答えられるように）。
- 供給方式: **P1 はオンザフライ再計算**（[_arbitration.md 決定7 L-13]）。常時計算テーブル（`indicators_daily`）は P2 以降で検討。

**`get_signals(date?, type?)`** — トップに `date`/`is_delayed`、行は名前を持たない（`company_name` はルータ JOIN）:
```jsonc
{
  "date": "2025-12-15",
  "is_delayed": true,
  "signals": [
    {"code": "72030", "company_name": "トヨタ自動車", "signal_type": "momentum", "score": 0.6, "payload": { /* §1.2 */ }}
  ]
}
```
- 行レベルに `date` は持たない（トップのみ）。`company_name` は signals JOIN stocks（[app] ルータ）が補完。`payload` に `label`/`change_5d` を quant が格納（§1.4・B-6）。

**`screen_stocks(criteria)`** — criteria キーは内部列名・各 item は `indicators`（`payload` ではない）:
```jsonc
// criteria = {signal_type?, sector33_code?, min_score?, limit?}   ← 内部列名
{
  "date": "2025-12-15",
  "is_delayed": true,
  "items": [
    {"code": "72030", "company_name": "トヨタ自動車", "signal_type": "momentum", "score": 0.6,
     "indicators": {"sma25": 2850.4, "sma75": 2790.1, "rsi14": 41.2, "vol_ma20": 1000000.0, "adj_close": 2901.0}}
  ]
}
```
- `min_score` は 0..1。`indicators` は `get_indicators` と同じ平坦な指標 dict。

| Tool 名 | 引数 | 供給元 | Phase |
|---|---|---|---|
| `get_indicators` | `code: str` | quant 純関数を最新日にオンザフライ再計算 | 1〜 |
| `get_signals` | `date: str\|None, type: str\|None` | repo.get_signals（トップ `date`/`is_delayed` は handler/ルータが付与・`company_name` は JOIN） | 1〜 |
| `screen_stocks` | `criteria: dict`（内部列名 `{signal_type?, sector33_code?, min_score?, limit?}`）| repo（signals × stocks join）＋ quant 指標 | 1〜 |

### 1.7 テストケース（ADR-016・既知系列 → 既知シグナル）

`backend/tests/test_quant_momentum.py` / `test_quant_volume_spike.py`（pytest・既存基盤は一時 SQLite だが quant 関数は DB 不要なので純関数テスト）:

- **GC 成立**: `sma25` が `sma75` を当日上抜けする手組み系列 → `golden_cross=True`・`score>=0.6`。
- **GC 非成立（既に上）**: ずっと `sma25>sma75` の系列 → `golden_cross=False`（「瞬間」のみ拾う確認）。
- **RSI 既知値**: Wilder RSI(14) の検証は**公開された定番系列**（例: Wilder 原典の 14 日例題、または `close=[44.34,44.09,...]` の有名サンプル）で `rsi≈70.53` 等の既知値に ±0.1 で一致。
- **RSI 反転**: RSI を 28→32 へ跨がせた系列 → `rsi_reversal=True`。
- **データ不足**: 50 行 → `None`（76 行未満）。
- **volume spike 成立**: 平常 100 万株 × 20 日 ＋ 当日 400 万株 → `ratio=4.0`・`score=0.4`。
- **volume フロア除外**: vol_ma20=3万株 → `None`（フロア未満）。
- **adj_close null**: 窓内に null → `None`（§0.1）。

### 1.8 P1 新規/変更ファイルと着工順

1. `pyproject.toml`: `numpy>=2.0`・`pandas>=2.2` 追加（ARM wheel 確認）。
2. `backend/app/quant/__init__.py`・`momentum.py`・`volume_spike.py`（純関数）。
3. `backend/tests/test_quant_*.py`（§1.7 を先に書く＝テスト駆動）。
4. `schema.py` に `signals` 追加 → Alembic `0003_signals`（down_revision=`0002_fetch_meta`）autogenerate（発行は data-arch）。
5. `repo.py` に `upsert_signals`/`get_signals`/`get_latest_signal_date`。
6. （[data-arch] 管轄）`app/batch/` から quant 関数を全銘柄ループで呼び `upsert_signals`。
7. （[app] 管轄）`/signals` REST ＋一覧画面・Tool 配線。

> **着工順の肝**: 純関数とテスト（1〜3）を先に固め、`signals` テーブル・バッチ・API は後。計算の真実（ADR-016）を最初に確定する。

---

## Phase 2: Portfolio Optimizer（相関・シャープ・最大DD・平均分散最適化・backtest）

[roadmap.md P2] / [advisor.md §4]（`get_portfolio_metrics`・`optimize_portfolio`）/ [ADR-013]（policy 制約）。

### 2.1 追加依存

```
scipy>=1.13           # 相関・統計
PyPortfolioOpt>=1.5   # 平均分散最適化（内部で cvxpy を使う）
# cvxpy は PyPortfolioOpt の依存として入る
```

**[OPEN] ARM ビルド（ADR-021）— 最重要確認点**: PyPortfolioOpt は最適化に **cvxpy**（C++ ソルバ ECOS/OSQP/SCS）を使う。

- 調査結果（2026-06）: **cvxpy 1.9.1 が manylinux aarch64 wheel を配布**し numpy2 にも対応済み。よって **pip で ARM wheel が入る見込み**。
- ただし PyPortfolioOpt が古い numpy/cvxpy にピン留めしていないか・aarch64 で実際に解けるかは **Docker クロスビルド（ADR-021）で実機確認**が必須。
- **代替案（cvxpy が ARM で詰む場合）**: 平均分散最適化は**自前で `scipy.optimize.minimize`（SLSQP）**でも実装可能（制約付き二次計画は SLSQP で解ける）。cvxpy の導入が ARM で破綻したら自前 SLSQP にフォールバックする（→ [data-arch]/[adr-guardian] と共有）。推奨は **まず PyPortfolioOpt を試し、ARM ビルドが通らなければ SLSQP 自前**。

### 2.2 ポートフォリオ・メトリクス（`get_portfolio_metrics` の実体）

**関数シグネチャ**（`backend/app/quant/portfolio.py`）:
```python
def compute_portfolio_metrics(
    price_panel: pd.DataFrame,   # index=date, columns=code, 値=adj_close（保有銘柄ぶん）
    weights: dict[str, float],   # 現在の構成比（時価ベース・0..1）
    policy: dict | None = None,  # 逸脱判定に使う（無ければ deviations は空）
    labels: dict[str, str] | None = None,  # code -> company_name（correlation.labels 用）
) -> dict:
    """相関行列・年率シャープ・最大ドローダウン・policy 逸脱を算出（純粋関数）。
    correlation は {codes, labels, matrix}、deviations は compute_deviations() で算出（§2.3a・B-12）。"""
```

**確定式**（すべて `adj_close` の日次リターンから・年率換算は **252 営業日**）:

| 指標 | 式 | 確定パラメータ |
|---|---|---|
| 日次リターン | `ret = price_panel.pct_change().dropna()` | — |
| 相関行列 | `ret.corr()`（ピアソン） | 保有銘柄間。ヒートマップ用 |
| 共分散（年率） | `ret.cov() * 252` | 最適化の入力にも使う |
| ポート日次リターン | `port_ret = (ret * w).sum(axis=1)` | w=weights ベクトル |
| 年率リターン | `port_ret.mean() * 252` | — |
| 年率ボラ | `port_ret.std(ddof=1) * sqrt(252)` | — |
| シャープレシオ | `(年率リターン - rf) / 年率ボラ` | **rf=0.0**（無リスク金利。日本はほぼ 0、明示する）。**[OPEN]** rf を policy か設定にするか（推奨: 当面 0.0 固定） |
| 最大ドローダウン | `cum = (1+port_ret).cumprod(); dd = cum/cum.cummax() - 1; mdd = dd.min()` | 累積リターンのピークからの最大下落率（負値） |

**返却 dict（= Tool `get_portfolio_metrics` の返却型・[_arbitration.md 決定2] が正本）**:
```jsonc
{
  "portfolio_id": 1,
  "as_of": "2025-12-15",
  "annual_return": 0.182,
  "annual_volatility": 0.255,
  "sharpe": 0.714,
  "max_drawdown": -0.213,
  "correlation": {
    "codes": ["72030", "67580"],
    "labels": ["トヨタ自動車", "ソニーG"],
    "matrix": [[1.0, 0.42], [0.42, 1.0]]
  },
  "lookback_days": 252,
  "is_delayed": true,
  "deviations": [
    {"kind": "position", "label": "72030 比率", "current": 0.182, "limit": 0.15, "breached": true}
  ]
}
```
- **correlation は `{codes, labels, matrix}`**（順序保証・UI 直結）。`codes[i]`/`labels[i]` が `matrix[i][j]` に対応。ネスト dict は採用しない（B-2）。
- **weight・correlation・deviations の current/limit は 0..1**（B-3）。
- **lookback**: 直近 **252 営業日（約 1 年）**。データが足りなければ取得できた日数で計算し `lookback_days` に実数を入れる。
- **`is_delayed`**: Free 12週遅延の明示（[data-model.md §3 holdings 注記]）。AI が「これは約3か月前の値」と添えられるように真偽値を返す。
- **`deviations`**: §2.3a の **単一関数 `compute_deviations()`** が供給（B-12）。`/asset-overview`（画面）と本 Tool の両方に**同値**を出す。`schema_version` は付けない（正本スキーマに無いため）。

### 2.3a deviations（policy 逸脱）の単一計算関数（B-12・[_arbitration.md 決定6]）

**deviations の計算は quant の単一関数に一本化**し、`/asset-overview`（画面・[app]）と `get_portfolio_metrics`（Tool・[ai-advisor]）の**両方へ同値を供給**する（計算は 1 か所・出力先 2 つ）。

**関数シグネチャ**（`backend/app/quant/portfolio.py`）:
```python
def compute_deviations(
    weights: dict[str, float],     # 現在の銘柄ウェイト（0..1）
    cash_ratio: float,             # 現在の現金比率（0..1）
    sector_weights: dict[str, float],  # 業種別合計ウェイト（0..1）
    policy: dict,                  # policy の構造化コア
    labels: dict[str, str] | None = None,  # code/sector -> 表示名
) -> list[dict]:
    """policy の各制約と現状の乖離を {kind,label,current,limit,breached} の配列で返す。
    current/limit は 0..1（B-3）。出力先は /asset-overview と get_portfolio_metrics の2つ（B-12）。"""
```

**出力要素**（`kind` ごと・current/limit はすべて 0..1）:

| kind | current | limit | breached の意味 |
|---|---|---|---|
| `position` | 各銘柄の現ウェイト | `max_position_weight` | 1 銘柄が上限超過 |
| `cash` | 現在の現金比率 | `target_cash_ratio` | 現金が目標を**下回る**（現金不足）|
| `sector` | 業種別合計ウェイト | `sector_caps[sector]` | 業種が上限超過 |

```jsonc
[
  {"kind": "position", "label": "72030 比率", "current": 0.182, "limit": 0.15, "breached": true},
  {"kind": "cash",     "label": "現金比率",   "current": 0.08,  "limit": 0.20, "breached": true},
  {"kind": "sector",   "label": "輸送用機器", "current": 0.31,  "limit": 0.30, "breached": true}
]
```
> `cash` の `breached` は「下回ると違反」（現金は最低ライン）、`position`/`sector` は「上回ると違反」（上限）。判定方向を kind ごとに固定する。

### 2.3 平均分散最適化（`optimize_portfolio` の実体）— policy 制約の写像

**関数シグネチャ**（`backend/app/quant/optimize.py`）:
```python
def optimize_portfolio(
    price_panel: pd.DataFrame,        # index=date, columns=code, adj_close（候補銘柄群）
    policy: dict,                     # policy 行（構造化コア）
    sectors: dict[str, str],          # code -> sector33_code（sector_caps 用）
    objective: str = "max_sharpe",    # 'max_sharpe' | 'min_volatility' | 'efficient_return'
) -> dict:
    """policy 制約下の最適ウェイトを返す（純粋関数。PyPortfolioOpt or 自前 SLSQP）。"""
```

**policy（[data-model.md §5]）→ 最適化制約への写像（[advisor.md §3] の二重活用を確定）**:

| policy 列 | 型 | 最適化制約への写像 | 備考 |
|---|---|---|---|
| `target_cash_ratio` | REAL（0〜1）| **株式合計ウェイト上限 = (1 - target_cash_ratio)**。残りは現金（最適化対象外） | 例 0.2 → 株式は最大 80% |
| `max_position_weight` | REAL（0〜1）| 各銘柄 `w_i <= max_position_weight` | PyPortfolioOpt の `weight_bounds=(0, max_position_weight)` |
| `sector_caps` | JSON `{sector33_code: cap}` | 業種ごと `sum(w_i for i in sector) <= cap` | PyPortfolioOpt の `add_sector_constraints(sector_mapper, lower={}, upper=sector_caps)` |
| `no_leverage` | INTEGER（0/1）| `w_i >= 0`（空売り禁止）＋ `sum(w) <= 1`（レバ無し） | 1 のとき long-only。ゼロカット解釈＝個別全損は許容だが借入なし（[ADR-013]） |
| `exclusions` | JSON `[code,...]` | 候補から除外（最適化に渡さない） | 銘柄を price_panel から落とす |
| `target_return` | REAL（任意）| objective が `efficient_return` のとき目標年率リターン | null なら max_sharpe |
| `risk_tolerance`/`time_horizon` | TEXT | **直接の制約にはしない**（objective 選択のヒント）| 高→max_sharpe、低→min_volatility 等。AI/設定が objective を選ぶ |

**返却 dict（= Tool `optimize_portfolio` の返却型・[_arbitration.md 決定2] が正本）**:
```jsonc
{
  "portfolio_id": 1,
  "as_of": "2025-12-15",
  "objective": "max_sharpe",
  "cash_weight": 0.20,
  "weights": [
    {"code": "72030", "current_weight": 0.12, "target_weight": 0.18, "delta": 0.06},
    {"code": "67580", "current_weight": 0.20, "target_weight": 0.15, "delta": -0.05}
  ],
  "expected_annual_return": 0.16,
  "expected_annual_volatility": 0.21,
  "expected_sharpe": 0.76,
  "constraints_applied": {
    "max_position_weight": 0.2, "target_cash_ratio": 0.2,
    "sector_caps": {"0050": 0.3}, "no_leverage": true, "exclusions": ["13010"]
  },
  "infeasible": false
}
```
- **`weights` は配列 `[{code, current_weight, target_weight, delta}]`**（順序安定・UI 直結。dict 返しは禁止＝B-2）。`delta = target_weight - current_weight`（リバランス差分）。`current_weight`/`target_weight`/`delta`/`cash_weight` はすべて **0..1**（B-3）。`schema_version` は付けない（正本スキーマに無い）。
- **`infeasible`**: 制約が厳しすぎて解が無い場合 `true`＋空 `weights`。AI が「制約が矛盾している（例 1銘柄上限 0.1 で 5 銘柄だと最大 0.5 しか張れず現金比率と衝突）」と説明できるように。
- 期待リターンの推定（**確定・L-14**）: **PyPortfolioOpt の `mean_historical_return`（年率）**＋共分散は **`CovarianceShrinkage().ledoit_wolf()`**（標本共分散は不安定なため Ledoit-Wolf 縮小推定）。将来 Black-Litterman（AI の見解を view に）も候補だが過剰なので後回し（[_arbitration.md 決定7 L-14]）。

### 2.4 バックテスト（⑥・主要指数との比較）

**関数シグネチャ**（`backend/app/quant/backtest.py`）:
```python
def backtest_portfolio(
    price_panel: pd.DataFrame,    # 候補/保有銘柄の adj_close
    weights: dict[str, float],    # 検証する固定ウェイト（最適化結果等）
    benchmark: pd.Series,         # 主要指数の水準（index_quotes 由来・TOPIX 等）
    rebalance: str = "none",      # 'none' | 'monthly'（初期は none=buy&hold）
) -> dict:
    """固定ウェイトを過去に当てはめ、累積リターン曲線・対ベンチ超過を返す（純粋関数）。"""
```
- **初期は buy & hold（rebalance='none'）**。月次リバランスは後付け（`monthly` 引数だけ予約）。
- 比較指標: 累積リターン曲線・年率リターン・シャープ・最大DD を**ポート vs ベンチ**で並べる。
- ベンチマークは `index_quotes`（[data-model.md §2]・**[data-arch] 管轄の IndexAdapter** が供給）。TOPIX を既定。
- **取引コストは控除しない（確定・L-15）**（[roadmap.md P7 留意点]と同じく提示用途。実弾運用時に要検証と注記）。手数料・スリッページは P2 は無視＋注記のみ（[_arbitration.md 決定7 L-15]）。

### 2.5 P2 テストケース

- シャープ: 既知の 2 資産・固定リターン系列 → 手計算したシャープと ±0.01 一致。
- 最大DD: 山→谷→山の系列 → 既知の MDD（例 -0.30）と一致。
- 最適化 long-only: `no_leverage=1` で全 w>=0 かつ sum(w)+cash=1。
- max_position_weight: 上限 0.2 で全 w<=0.2。
- sector_caps: 同業種合計が cap 以下。
- infeasible: 矛盾制約（max 0.1 × 3 銘柄 + cash 0.8）→ `infeasible=true`。
- backtest: buy&hold の累積リターンが手計算と一致・ベンチ超過列が正しい符号。

### 2.6 P2 新規/変更ファイル・着工順

1. `pyproject.toml`: scipy・PyPortfolioOpt 追加（**ARM ビルド実機確認が最初のゲート**）。
2. `quant/portfolio.py`（metrics）→ test → 3. `quant/optimize.py`（最適化＋policy 写像）→ test。
3. `quant/backtest.py` → test（IndexAdapter の index_quotes が前提＝[data-arch] と順序調整）。
4. Tool `get_portfolio_metrics`/`optimize_portfolio` 配線（[ai-advisor]）。

---

## Phase 5: AI Alpha Scorer（LightGBM 決算スコア）

[roadmap.md P5] / [ADR-006]（学習は別 PC・ラズパイは `.pkl` 推論のみ）/ [advisor.md §4]（`get_signals` の ai_alpha）。

### 5.1 役割分担（ADR-006 の厳守）

- **学習（別 PC）**: `financials` ＋将来リターンで LightGBM を学習し `model.pkl` を出力。学習スクリプトは `backend/app/quant/ml/train.py`（**ラズパイでは実行しない**・再現手順を docs 化）。
- **推論（ラズパイ夜間バッチ）**: `model.pkl` をロードし全銘柄スコア算出 → `signals`（`signal_type='ai_alpha'`）へ UPSERT。推論モジュール `backend/app/quant/ml/infer.py`。
- `lightgbm` は **推論にも import が要る**ため backend 依存に入るが、**ARM ビルド難（[ADR-021]）**。クロスビルド（別 PC でイメージ作成）前提。**[OPEN]** lightgbm の aarch64 wheel が pip で入るか実機確認（入らなければ ONNX 等への変換で推論だけ軽量化する案＝過剰なので最後の手段）。

### 5.2 特徴量設計（financials ＋ price）

**[OPEN] ラベル（教師信号）の定義** — ユーザー裁定が要る根幹:
- 推奨: **決算開示後 N 営業日の超過リターン**（対 TOPIX）。N の候補: 20 / 60 営業日。推奨 **60 営業日（約 3 か月）**の対ベンチ超過リターンを**回帰**で予測（分類より情報量が多い）。
- 代替: 「翌四半期に市場を上回ったか」の 2 値分類（解釈が楽）。
- **[OPEN]** 回帰 or 分類、ラベル期間（20/60日）はユーザー方針次第。推奨は 60 日超過リターンの回帰。

**特徴量（`financials` 由来・[data-model.md §2]）**:
| 特徴量 | 定義 | 出所 |
|---|---|---|
| 売上成長率 | YoY `net_sales` 変化率 | financials |
| 営業利益成長率 | YoY `operating_profit` 変化率 | financials |
| 純利益成長率 | YoY `profit` 変化率 | financials |
| 営業利益率 | `operating_profit / net_sales` | financials |
| EPS 成長率 | YoY `eps` 変化率 | financials |
| PER | `adj_close / eps`（開示日基準） | financials × daily_quotes |
| PBR | `adj_close / bps` | financials × daily_quotes |
| サプライズ代理 | 開示日±数日の adj_close リターン | daily_quotes |
| モメンタム | 開示日時点の 3 か月 adj_close リターン | daily_quotes |

- **リーク防止（最重要）**: 特徴量は**開示日 `disclosed_date` 時点で既知の情報のみ**。ラベル（将来リターン）の期間と特徴量の期間が重ならないこと。backtest 同様 point-in-time で組む。
- **[OPEN]** 財務の YoY を取るには複数期の financials が要る（Free は約 2 年分）。学習データ量が薄い可能性。データ量の確認は P5 着手時（financials 取得は [data-arch]・P2/P5）。

### 5.3 signals 書き込み形（`signal_type='ai_alpha'`）

```json
{
  "date": "2025-12-15",
  "code": "72030",
  "signal_type": "ai_alpha",
  "score": 0.73,
  "payload": {
    "predicted_excess_return_60d": 0.082,
    "model_version": "ai_alpha_v1_20251201",
    "feature_snapshot": {"sales_growth_yoy": 0.12, "per": 14.2, "...": 0},
    "schema_version": 1
  }
}
```
- `score` は予測値を **0〜1 に正規化**（全銘柄の予測超過リターンを当日内でランク正規化＝パーセンタイル）。生の予測値は payload に保持。
- `model_version` を必ず残す（どのモデルが出したスコアか監査・再現用＝ADR-016 の再現性）。

### 5.4 再現手順（docs 化必須・ADR-006 完了条件）

`docs/ml-training.md`（新規・**[lead] へ依頼**）に:
1. 学習データの抽出 SQL（point-in-time）。
2. 特徴量・ラベルの定義（§5.2 確定値）。
3. `train.py` の実行コマンド・ハイパラ（LightGBM の `num_leaves`/`learning_rate`/CV 設定）。
4. 出力 `model.pkl` の置き場所とラズパイへのコピー手順。
5. 評価指標（回帰: RMSE/IC〔情報係数〕、分類: AUC）。

### 5.5 P5 テストケース

- 推論の決定性: 同じ `model.pkl` ＋同じ特徴量 → 同じ score（ADR-016）。
- 特徴量組み立て: 既知の financials 行 → 期待する特徴量ベクトル。
- リーク検査テスト: ラベル期間と特徴量期間が重ならないことを assert する仕組み。
- score 正規化: 既知の予測値分布 → 期待パーセンタイル。

---

## Phase 7: Sector Lead-Lag（参考・本レーン管轄の数理部分のみ）

[roadmap.md P7]（部分空間正則化付き PCA・SIG-FIN-036）/ [ADR-009]。米国データ取得は [data-arch]（UsEquityAdapter）。本レーンは**数理（PCA 低ランク予測器）**を担当。

- **概要**: 日米業種 ETF 日次リターンの結合相関を事前部分空間へ正則化 → 固有分解 → 翌営業日の日本業種スコアを `signals`（`signal_type='lead_lag'`・`code` に業種コード）へ。
- **関数**（`backend/app/quant/lead_lag.py`）: `compute_lead_lag(jp_sector_returns, us_sector_returns, lambda_=0.9, k=3) -> dict`（λ=正則化強度・K=部分空間ランクは論文準拠。**[OPEN]** λ/K の確定値は P7 着手時に論文と実データで調整）。
- 依存: numpy/scipy（既存）。追加なし。日足のみ・軽量でラズパイ夜間バッチに適合（[roadmap.md P7]）。
- **留意（[roadmap.md P7]）**: 論文は取引コスト控除後の超過収益を明示せず。提示用途では軽視可、実弾運用時は要検証と注記。
- 詳細設計は P7 着手時に別途（本ドラフトでは scope と signal 書き込み形〔上記 `lead_lag`〕の予約まで）。

---

## 横断: 他レーン依存・要調整サマリ

- **[data-arch]**: ① numpy/pandas/scipy/PyPortfolioOpt/lightgbm の **ARM クロスビルド実機確認**（ADR-021）。② 夜間バッチが quant 純関数を全銘柄ループで呼び `upsert_signals` する（呼び出し側）。③ `financials`（P5）・`index_quotes`（P2 backtest ベンチ）の取得。④ adapter に**調整 OHLV 列**を足すか（§0.1・§9）。
- **[ai-advisor]**: Tool 契約は **[_arbitration.md 決定2] が正本**で本書はそれに完全一致済み（`get_indicators` 平坦・`get_signals`/`screen_stocks` ラップ・correlation `{codes,labels,matrix}`・weights 配列・`is_delayed`/`as_of`）。`get_indicators` の供給方式は **P1 オンザフライ再計算で確定**（[_arbitration.md 決定7 L-13]）。
- **[app]**: `/signals` REST の入出力（signals 行 ＋ stocks join）・一覧画面。本書 §1.4 の DDL と §1.5 repo を契約面とする。
- **[adr-guardian]/[lead]**: **[DOCS要修正]** — (a) data-model.md §4 の signals に `(date,code,signal_type)` UNIQUE 追記（冪等）。(b) §0.1 の「adj_close のみ保存で high/low 系指標が組めない」点（adapter に調整 OHLV を足すか、ATR 系を当面非対応とするかを docs に明記）。

## [OPEN] 一覧（R3 後の状態）

### ユーザー裁定が要る（`_open-questions.md` で確認・[_arbitration.md 決定8]。推奨値を spec のデフォルトに採用済み・env/設定で差替可）

1. **momentum スコア重み**（U-1）GC0.6/RSI0.4（既定）vs 等加重 vs GC のみ。
2. **volume_spike 閾値**（U-2）3.0 倍（既定）/ スコアスケール ÷10（既定）。
3. **rf（無リスク金利）**（U-3）0.0 固定（既定）vs policy/設定化。
4. **P5 ラベル**（U-4）60日超過リターンの回帰（既定）vs 20日 / 2値分類。

### lead 裁量で確定済み（[_arbitration.md 決定6/7]・R3 で解決）

- **adj_close 欠損 = skip**（補間しない＝数字を作らない・L-26）。
- **get_indicators = P1 オンザフライ再計算**（L-13）。
- **期待リターン推定 = historical mean + Ledoit-Wolf**（L-14）。
- **backtest 手数料 = 無視＋注記**（L-15）。
- **依存追加判断=quant／クロスビルド段取り=data-arch**（決定6）。

### 技術リスク（要実機確認・ユーザー裁定ではない）

- **依存の ARM ビルド**: PyPortfolioOpt/cvxpy（cvxpy 1.9.1 が aarch64 wheel あり）・lightgbm が aarch64 で通るか。詰めば cvxpy→自前 SLSQP、lightgbm→クロスビルド／最終手段 ONNX。Phase 1〜2 着手時に data-arch と実機検証。
