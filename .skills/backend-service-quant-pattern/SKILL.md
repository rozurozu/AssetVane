---
name: backend-service-quant-pattern
description: services/（下ごしらえ・オーケストレーション）と quant/（数理計算の純関数）を新規作成・修正するときに必ず使う。計算境界の規律(ADR-014/016)＝quant は DB を知らない純関数・services が DB と quant の間で整形しオーケストレーションする・AI も router も計算しない・手法は必ずテスト済みコードで実装する、を規定する。
---

# service / quant 規約（計算境界）

数値計算の所在を厳密に分ける。これは ADR-014（AI に数値を計算させない・Python が事実を計算する）と ADR-016（手法はテスト済みコードで実装し再現性・backtest を保つ）の実装規律。

```
repo (dict) ──→ services（下ごしらえ：DataFrame 化・ウェイト算出 等）──→ quant（純関数：相関・最適化・指標）──→ services/router（結果を組み立て）
```

## quant/ — 純関数

- **DB を知らない**。引数は `pd.DataFrame` / `dict` / `list` 等の素のデータ、戻り値も素のデータ（`dict`・`DataFrame`・スカラ）。`Connection` を受け取らない。`repo` を import しない。
- **副作用を持たない純関数**。同じ入力に同じ出力。乱数を使うなら seed を引数で受ける。
- 使うのは `pandas` / `numpy` / `scipy` 等。**手法（モメンタム・一目均衡表・相関・平均分散最適化・リードラグ等）は必ずここにテスト済みコードとして実装**する。LLM にその場で計算式を書かせない（再現性・backtest が壊れる＝ADR-016）。
- **入力 DataFrame を破壊変更しない**。`df.loc[mask, "col"] = x` を使い（chained assignment は避ける）、必要ならコピーして返す（pandas CoW 将来警告対策）。
- **欠損は NaN のまま**。`fillna`/`interpolate` で埋めない（事実を捏造しない・ADR-014）。相関などは pandas が NaN をペアワイズ除外するのでそのまま渡す。
- **データ不足は安全な既定を返す**（保有 1 銘柄・履歴不足なら `None`/空を返す）。例外で落とさず、呼び出し側がそのまま通せるようにする。

```python
def compute_correlation(panel: pd.DataFrame) -> dict[str, Any]:
    """adj_close パネル（index=date, columns=code）から相関行列を返す純関数（ADR-016）。"""
    if panel.shape[1] < 2:
        return {"codes": [], "labels": [], "matrix": []}  # 1 銘柄以下は空
    returns = panel.pct_change()
    corr = returns.corr()  # NaN はペアワイズ除外
    ...
```

## services/ — 下ごしらえとオーケストレーション

- **DB（repo）と quant の間に立つ**。repo の dict を quant が食える形（DataFrame パネル・ウェイト dict・labels 等）に整え、quant を呼び、必要なら複数 quant・複数 repo を正しい順序で組み合わせる。
- **数値計算そのものは quant に委ねる**。services で相関やシャープを手計算しない（下ごしらえと組み立てだけ）。
- 価格パネルの組み立ては `pd.concat(series, axis=1, join="outer")`（各 code の Series を外部結合、欠損は NaN）。`pivot` を使うなら (date, code) 重複が無い前提（UPSERT で重複は無い）。

```python
def build_price_panel(conn: Connection, codes: list[str]) -> pd.DataFrame:
    """codes の adj_close を日次パネル(index=date, columns=code)で返す（欠損は NaN・補間しない）。"""
    frames: dict[str, pd.Series] = {}
    for code in codes:
        quotes = repo.get_quotes(conn, code)
        if quotes:
            frames[code] = pd.Series({q["date"]: q["adj_close"] for q in quotes}, name=code, dtype=float)
    if not frames:
        return pd.DataFrame()
    panel = pd.concat(frames, axis=1)
    panel.index = pd.Index(sorted(panel.index))
    return panel
```

- 読み取り接続は引数の `conn` を使う（router から渡る＝[[backend-repo-pattern]]）。services で接続を勝手に開かない（書き込みオーケストレーションで境界を所有する W2 ケースを除く）。
- **同じ事実は 1 か所で計算して複数の出力先に配る**。同値であるべき指標（例: policy 逸脱を画面 API と Tool の両方が返す）を別々に組まず、共有 service 関数に一本化する（食い違いを防ぐ）。

## 導出値は導出関数経由でのみ更新

transactions → holdings のような**導出値**は、元データから再計算する関数（`recalc_*`）経由でのみ更新する。導出先テーブルを直接編集しない（ADR-019 系の規律）。再計算は元データを時系列順に畳んで状態を作り、結果を `replace_*` で入れ替える。

## AI・router は計算しない（再掲・最重要）

- LLM は Tool で受け取った**事実を解釈・提案するだけ**。プロンプトに生データを丸投げして計算させない（ADR-014）。
- router も計算しない（[[backend-router-pattern]]）。計算は必ず quant。

## チェックリスト

- [ ] quant 関数は DB を知らない純関数（`Connection`/`repo` を import していない・副作用なし）
- [ ] 手法は quant にテスト済みコードとして実装（LLM にその場計算をさせていない）
- [ ] 入力 DataFrame を破壊変更していない。欠損を埋めていない（NaN のまま）
- [ ] データ不足時に quant が安全な既定（None/空）を返す（例外で落ちない）
- [ ] services は下ごしらえ＋オーケストレーションのみ（数値計算を手書きしていない）
- [ ] 同値であるべき指標は共有 service 関数に一本化（別々に組んでいない）
- [ ] 導出値は再計算関数経由で更新（導出先を直接編集していない）
- [ ] docstring 冒頭に ADR-014/016・spec 参照
