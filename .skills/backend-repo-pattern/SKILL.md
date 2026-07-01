---
name: backend-repo-pattern
description: db/repo/ パッケージのクエリ関数や db/schema.py の Table 定義を新規作成・修正するとき必ず読む（SQLAlchemy Core・UPSERT 冪等・読み書きの接続規律・ドメイン別サブモジュール）。
---

# repo / schema 規約

`db/repo/`（クエリ・ドメイン別サブモジュールのパッケージ）と `db/schema.py`（Table 定義）。**SQLAlchemy Core のみ（ORM は使わない）**。戻り値は**素の `dict`**（Pydantic 変換は router の責務＝[[backend-router-pattern]]）。

## db/repo/ パッケージ構成

`db/repo` は単一ファイルではなく**ドメイン別サブモジュールのパッケージ**（`stocks.py` / `valuation.py` / `portfolio.py` / `advisor.py` / `news.py` / `themes.py` / `us_equity.py` …）。境界は spec の Phase / ドメインに沿う（迷ったら現存の `# =====` 見出しが分類の手本）。

- **横断ヘルパは `_common.py`**: 全ドメインで使う `_upsert`（汎用 UPSERT）や `pack_embedding`（embedding BLOB 化）はここに置き、各サブモジュールが `from app.db.repo._common import _upsert` で取る。ドメイン局所の private ヘルパ（`_valuation_inner_subquery` 等）や定数（`_SCREEN_SORT_COLS` 等）は所属サブモジュールに置く。
- **呼び出し側は従来どおり `repo.func()`**: `__init__.py` が全 public 関数を明示名 import ＋ `__all__` で flat に再エクスポートする（`import *` は使わない＝house style）。新関数を足したら `__init__.py` の import と `__all__` にも 1 行追加する（pyright/ruff の F401 を満たし、`repo.新関数` で引けるようにする）。public ヘルパ（`pack_embedding` 等）も再エクスポートに含める。private（`_` 始まり）は再エクスポートしない。
- **サブモジュール間は原則疎結合**: ある関数が別ドメインの関数を呼ぶ必要が出たら、まず**呼ばれる側に co-locate できないか**を検討する（例: `get_max_daily_date` は `get_max_quote_date` の別名なので `stocks.py` に同居）。やむを得ず跨ぐ時だけ `from app.db.repo.<mod> import ...`（`_common` は repo パッケージを import しない＝循環を作らない不変条件）。
- **新規追加時の置き場所**: 既存ドメインに合うサブモジュールへ。新ドメインなら新サブモジュール＋`__init__.py` 配線。サブモジュールが肥大化したら同方針で更に割ってよい（呼び出し側は `repo.func()` のままなので無影響）。

## schema.py

- スキーマは `db/schema.py` の `Table` 定義に一元化する（`metadata` に登録）。列名は**安定した内部名**にする（外部 API のキー名はここに持ち込まない＝[[backend-adapter-pattern]]）。
- スキーマ変更は Alembic のリビジョンに刻む（autogenerate）。baseline は `metadata.create_all` 方式。

## クエリの書き方（2.0 スタイル）

- `select()` / `insert()` の 2.0 スタイル。`engine.execute()` 直叩きは不可。必ず `Connection` 経由。
- **戻り値を素 dict にするには `.mappings()`**。`conn.execute(select(...)).mappings().all()`（または `.first()`）で dict ライクの行を得て、`dict(row)` 相当で返す。
- 名前など別テーブルの値は **JOIN で補完**して返す（例: `signals JOIN stocks` で `company_name`）。行レベルに名前を焼かず、読むときに結合する。
- 文字列の JSON（TEXT 列）は**パースせず生のまま返す**。`json.loads` は router の責務。
- **window 関数・集約の戻り型は Float 化する**。`func.percent_rank()` / `cume_dist()` 等は SQLAlchemy が `Numeric(asdecimal=True)` と解釈し **`Decimal` を返す**。この行が AI Advisor の Tool 経由で LLM/MCP 境界に渡ると `json.dumps` が `Decimal` を直列化できず **500 になる**（実障害あり）。`type_coerce(func.percent_rank().over(...), Float())` で Float 化し素の `float` で返す（出所を断つ＝[[advisor-tool-pattern]] の「返り値は JSON-safe」の repo 側の担保）。`func.row_number()`（int）は対象外。

## 読み取り = 注入 conn・commit しない

```python
def list_stocks(conn: Connection, q: str | None = None) -> list[dict[str, Any]]:
    stmt = select(stocks)
    if q:
        stmt = stmt.where(...)
    return [dict(r) for r in conn.execute(stmt).mappings().all()]
```

- 読み取り関数は **`conn: Connection` を引数で受け、トランザクションを開かない**。呼び出し元（router の `Depends(get_conn)` / service / バッチ）が接続寿命を所有する。read だけなので commit 不要。

## 書き込み = 二階規約（W1 / W2）

書き込みは**2 通りを意図的に使い分ける**。新規追加時はどちらに当たるか判断してから書く。

### W1: バルク / 冪等 UPSERT は repo が自前で `engine.begin()`

夜間バッチの大量 UPSERT や、API からの単発の単純な書き込み（現金残高・外部資産 1 件など、1 文で閉じる書き込み）は、**repo 関数内で `with get_engine().begin() as conn:` を開いて commit まで完結**する。呼び出し側は rows を渡すだけ。バッチのジョブが毎回 begin を書かずに済む。

注意: `transactions → holdings` のように導出値の再計算を伴う書き込みは「取引 1 件」でも W1 ではない。`transactions` だけが commit 済みで `holdings` 再導出に失敗する中間状態を避けるため、W2 として同じ transaction に束ねる。

```python
def upsert_daily_quotes(rows: list[dict[str, Any]]) -> int:
    stmt = sqlite_insert(daily_quotes).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["code", "date"],
        set_={c: stmt.excluded[c] for c in ("open", "high", "low", "close", "volume", "adj_close")},
    )
    with get_engine().begin() as conn:  # 成功で commit / 例外で rollback
        result = conn.execute(stmt)
    return result.rowcount
```

### W2: 1 リクエストで複数表を atomic に書く系は呼び出し側が begin を所有

advisor 系や取引記録系のように、1 リクエストで複数テーブル（transactions ＋ holdings、proposal ＋ journal ＋ llm_usage 等）を**まとめて atomic に**書くものは、**repo 関数は `conn` を受け取り execute だけ**して**自分では commit しない**。呼び出し側（router/service）が `with get_engine().begin() as conn:` で境界を所有し、複数 repo 呼び出しを 1 トランザクションに束ねる。

```python
def insert_proposal(conn: Connection, **fields: Any) -> int:  # commit しない
    result = conn.execute(insert(proposals).values(**fields))
    return int(result.inserted_primary_key[0])

# 呼び出し側（router/service）が境界を所有し、複数表を 1 トランザクションに束ねる
with get_engine().begin() as conn:
    pid = repo.insert_proposal(conn, ...)
    repo.insert_journal(conn, ...)
    repo.insert_llm_usage(conn, ...)
```

W2 関数の docstring/付近に「commit はしない。呼び出し側が `with get_engine().begin()` で所有する」と明記する。

### どちらにするかの判断

- **単発・冪等・1 文で閉じる** → W1（repo が自前 begin）。バッチからも API からも引数を渡すだけで呼べる。
- **1 リクエストで複数表を atomic に**、または**導出値の再計算を同時に行う**（中途半端な状態を残せない） → W2（呼び出し側が begin 所有）。
- **性能目的の W2**: atomic 要件が無くても、**1 回の処理（API 1 コール等）が複数行を返し、それを束ねて書く**なら W2 にしてよい。行ごとに W1（自前 begin）を呼ぶと begin/commit を行数ぶん繰り返し、SQLite WAL の読×書競合窓も行数ぶん開く。バッチ単位で 1 begin に束ねると競合窓と開閉オーバーヘッドが縮む。実例＝`update_news_embedding` / `update_theme_embedding`（embeddings API がバッチ 100 件を返す→1 begin で 100 行 UPSERT）。逆に**1 行ごとに 1 API/計算**（例: テーマタガーは銘柄ごと LLM 呼び）なら束ねる単位が無いので W1 で素直。
- 迷ったら「この書き込みは他の書き込みと**まとめて 1 トランザクションにしたい**か？」で決める。Yes なら W2。

## UPSERT で冪等（ADR-002）

- 書き込みは **`on_conflict_do_update` で冪等**にする。再取得・再実行で重複や破壊が起きないようにする。
- **SQLite 方言固有 import が必須**: `from sqlalchemy.dialects.sqlite import insert as sqlite_insert`。汎用 `sqlalchemy.insert` には `on_conflict_*` が無い。挿入予定行は `stmt.excluded.col`（特殊名は `stmt.excluded["col"]`）。
- WAL・`busy_timeout` 等の pragma は engine の `connect` イベントで設定済み（書き手を基本 1 つに寄せる方針＝ADR-002）。

## チェックリスト

- [ ] SQLAlchemy Core のみ（ORM を使っていない）。戻り値は素の dict（`.mappings()`）
- [ ] 名前等は JOIN で補完。JSON(TEXT) はパースせず生で返す（パースは router）
- [ ] 読み取り関数は `conn` を受け取り commit しない
- [ ] 書き込みは W1（自前 `engine.begin`）/ W2（`conn` 受け取り・呼び出し側 begin）のどちらか正しい方を選んだ
- [ ] W2 関数は「commit しない・呼び出し側が境界所有」を明記
- [ ] UPSERT は `sqlalchemy.dialects.sqlite.insert` ＋ `on_conflict_do_update` で冪等
- [ ] schema 変更は schema.py ＋ Alembic リビジョンに刻んだ。列は内部名
