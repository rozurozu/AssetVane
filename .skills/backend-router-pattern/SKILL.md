---
name: backend-router-pattern
description: backend の REST ルータ（app/routers/ 配下・advisor 等のサブパッケージ router 含む）を新規作成・修正するときに必ず使う。HTTP 入出力のみを持つ薄い層として、APIRouter・Pydantic 入出力モデル・接続注入・response_model・例外の HTTPException 翻訳・境界処理を規定する。ロジック・数値計算・DB クエリ詳細は持たせない。
---

# REST ルータ規約

`app/routers/`（および `advisor/` 等のサブパッケージの `router.py`）。**HTTP の入出力だけ**を持つ薄い層。ロジックは services/quant/repo に出す（[[backend-foundations]] のレイヤ表）。

## 基本形

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import Connection

from app.db import repo
from app.db.engine import get_conn

router = APIRouter(tags=["stocks"])


class Stock(BaseModel):
    code: str
    company_name: str | None = None


@router.get("/stocks", response_model=list[Stock])
def list_stocks(
    q: str | None = Query(default=None, description="コード/銘柄名の部分一致"),
    conn: Connection = Depends(get_conn),
) -> list[Stock]:
    return [Stock(**row) for row in repo.list_stocks(conn, q)]
```

- `router = APIRouter(tags=[...])`。`app.main` で `include_router` する。
- **同期 `def`**（[[backend-foundations]]）。`await` I/O を内部に持つ口（LLM 呼び出し等）だけ `async def`。
- docstring 冒頭に `docs/api.md` の該当節・ADR を引く。**Phase 2/3 など先行 Phase のルータは `docs/api.md` がまだ未更新のことがある。その場合は `docs/phase-specs/*` の該当節を docstring 参照源にしてよい**（`docs/api.md` と等価に扱う。後で `docs/api.md` に反映する前提）。

## 接続の注入（読み取り）

- 読み取りは **`conn: Connection = Depends(get_conn)`** でリクエストスコープの接続を受ける。`get_conn` が `with get_engine().connect() as conn: yield conn` で寿命を所有し、レスポンス後に確実に閉じる。router は接続を**開閉しない**。
- repo の読み取り関数に `conn` を渡すだけ（[[backend-repo-pattern]]）。router で `with engine.begin()` を書かない（書き込み時の境界所有は repo 規約に従う）。
- `async def` の口では dep に頼らず、関数内で `with get_engine().connect() as conn:` を短く開閉してよい（同期接続を非同期で扱うため）。

> 補足: 同じ dep を多用するなら `Annotated[Connection, Depends(get_conn)]` のエイリアスにしてもよい。現行は `conn: Connection = Depends(get_conn)` で統一している。

## Pydantic 入出力モデル

- 入出力モデルは **router ファイル内に inline 定義**し、`docs/*-spec.md` の TS 型と **1:1**（フィールド名・null・単位）にする。frontend の `lib/api.ts` 型ともそろえる（[[frontend-api-client-pattern]]）。
- **`response_model=...` をデコレータに付ける**。repo が素の dict を返しても response_model が出力スキーマに絞る（余剰キーは落ちる）。これで repo は dict のまま・router は Pydantic で公開、と分離できる。
- 受けは Pydantic モデル（POST body）/ `Query` / `Path`。`Literal` で列挙的パラメータ（`signal_type` 等）。比率・weight は **0..1**（×100 は UI 側・ADR-008）。
- 追加キーを素通ししたい payload（type 固有指標等）は `model_config = ConfigDict(extra="allow")`。API 契約を締めたい応答は既定（または `extra="forbid"`）。

## dict → Pydantic 変換は router の責務

- repo は素の dict を返す。**`Model(**row)` / `[Model(**r) for r in rows]` で変換するのは router**。
- **JSON 文字列のパースも router 側**（repo は TEXT のまま返す契約）。壊れた JSON は事前計算側のバグなので握りつぶさず 500 に翻訳:

```python
try:
    parsed = json.loads(raw) if raw else {}
except (TypeError, ValueError) as exc:
    raise HTTPException(status_code=500, detail="payload の JSON が不正です。") from exc
```

## 例外の翻訳

- 見つからない → `raise HTTPException(status_code=404, detail="...")`。
- services/adapters が投げる**独自例外を境界で翻訳**する（上流失敗=502・コスト上限=429・競合=409 等）。`from exc` で連鎖を残す（[[backend-foundations]]）。
- ビジネス上の「あり得る」結果（最適化 infeasible 等）は**エラーにせず** 200 で結果フラグとして返す（spec に従う）。

## ロジックを持たせない

- **数値計算をしない**（quant へ）。**DB クエリの詳細を書かない**（repo へ）。**外部 API を直叩きしない**（adapter へ）。
- 複数ステップの段取り（取得→整形→計算→組み立て）は services に出し、router はその結果を Pydantic に詰めて返すだけにする。router 内の private ヘルパは「既定 portfolio 解決」「レスポンス組み立て」など HTTP 寄りの薄いものに留める。

## チェックリスト

- [ ] `APIRouter(tags=[...])`・同期 `def`（async は await I/O のある口だけ）
- [ ] 読み取りは `Depends(get_conn)` で接続注入。router で接続を開閉していない
- [ ] 入出力 Pydantic モデルは spec と 1:1（フィールド名・null・単位 0..1）。`response_model` を付けた
- [ ] dict→Model 変換・JSON パースを router で行い、壊れ JSON は 500 に翻訳
- [ ] 独自例外を境界で `HTTPException` に翻訳（`from exc`・適切なステータス）
- [ ] 数値計算・DB クエリ詳細・外部 API 直叩きを router に書いていない
- [ ] docstring 冒頭に `docs/api.md` / ADR 参照
