---
name: backend-adapter-pattern
description: adapters/ の外部データソースクライアント（JQuants/Index/UsEquity/News/Fx 等）を新規作成・修正するときに必ず使う。外部 API を直結ハードコードせずアダプタ越しにする(ADR-010)・「外部キー名→内部列名」の対応をアダプタ内に閉じ込める・リトライ/スロットル/ページングをアダプタ内に隠す・用途別の独自例外、を規定する。
---

# 外部データソースアダプタ規約

外部 API（J-Quants・指数・米株・ニュース・為替 等）は**アダプタ越しに使う**。router/service/batch から外部 API を直結ハードコードしない（ADR-010）。アダプタは「外の世界の都合」を 1 ファイルに閉じ込め、内側には安定したデータだけを渡す。

## アダプタの責務

- **外部 API へのアクセスを一手に引き受ける**。HTTP・認証・ページング・リトライ・スロットルをアダプタ内に隠す。呼び出し側は `code` や期間を渡すと正規化済みの行が返る、という契約だけ見る。
- **「外部キー名 → 内部列名」の対応をこのファイルに閉じ込める**。DB の列は安定した内部名のまま保ち、外部 API のフィールド名（略記・命名変更・バージョン差）を内側に漏らさない。
- データソースごとに 1 アダプタ（`JQuantsAdapter` / `IndexAdapter` / ...）。

## キー名の正規化（外→内の境界）

外部レスポンスのフィールド名は変わりうる（V1/V2 差・略記）。**候補キーのフォールバック**で吸収し、内部列名に正規化する。

```python
def _first(d: dict[str, Any], keys: list[str], default: Any = None) -> Any:
    """候補キーのうち最初に存在した値を返す（略記/フルネーム両対応）。"""
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default

# 正規化: 外部キー（候補複数）→ 内部列名（安定）
row = {
    "code": _to_internal_code(_first(raw, ["Code", "code"])),
    "date": _norm_date(_first(raw, ["Date", "date"])),
    "adj_close": _first(raw, ["AdjustmentClose", "AdjC", "adj_close"]),
}
```

- 日付・コード等の形式正規化（`YYYYMMDD`→`YYYY-MM-DD`、4 桁→5 桁等）もアダプタ内のヘルパで行う。
- 正規化後の行は[[backend-repo-pattern]] の UPSERT にそのまま渡せる内部列名にする。

## リトライ・スロットル・ページング

- **スロットル**（レート制限対策）をアダプタ内に持つ。前回リクエストからの最低間隔を `time.monotonic()` で計り `time.sleep` で待つ。間隔は設定（`settings`）から読み、モジュール定数はフォールバック既定にする。
- **リトライ**は 429/一時失敗に指数バックオフ。最大回数を定数で持つ。
- **ページング**（`pagination_key` 等）はアダプタ内で辿って全行を集約し、呼び出し側に見せない。
- エンベロープからの行抽出（`{"data": [...], "pagination_key": ...}`）もアダプタ内のヘルパに。

## 設定・認証・例外

- 認証情報は `settings`（backend の `.env`）から読む。**キーをハードコードしない・frontend に渡さない**。J-Quants は V2（`x-api-key` ヘッダ）を使う（ADR-008）。
- 未設定・取得失敗は**用途別の独自例外**（例 `JQuantsError(RuntimeError)`）で投げ、メッセージに対処（`.env` の設定等）を書く。呼び出し側（router/batch）が翻訳する（router は HTTPException、batch は JobResult/通知）。
- HTTP クライアントは `httpx`。`with httpx.Client(...) as client:` でタイムアウトを設定。

## チェックリスト

- [ ] 外部 API アクセスをアダプタに閉じ込めた（router/service/batch から直結していない）
- [ ] 外部キー名→内部列名の正規化をアダプタ内に閉じ込めた（候補キーのフォールバック・形式正規化）
- [ ] リトライ・スロットル・ページングをアダプタ内に隠した（設定値は `settings` から）
- [ ] 認証情報は `settings` から（ハードコード・frontend 露出なし）。J-Quants は V2 `x-api-key`
- [ ] 失敗は用途別の独自例外（対処を含むメッセージ）。呼び出し側で翻訳
- [ ] 正規化後の行が repo の UPSERT にそのまま渡る内部列名
- [ ] docstring 冒頭に ADR-008/010 参照
