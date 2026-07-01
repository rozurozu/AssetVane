# 知識ノートの銘柄スコープ 設計サマリ（ADR-062 追補ドラフト）

作成: 2026-07-02 / 方法: ユーザーとの設計相談（grill-me・設計ツリーを 9 分岐で降下）＋実装の裏取り（`db/schema.py`・`db/repo/knowledge_cards.py`・`services/knowledge_cards.py`・`advisor/router.py`・`advisor/nightly.py`・`advisor/prompt_builder.py`・`routers/cards.py`・`advisor/tools/schemas.py`）。基点コミット: `4f6ed808`（ADR-071・遅延誤注記の是正）。

> **このファイルの位置づけ**: 「個別銘柄特有の知見（アノマリー等）をどこに置くか」の設計合意の正本。ドシエ（`stock_dossiers`）との切り分けと、`knowledge_cards` を銘柄粒度に拡張する設計を確定した。ADR は 067 までではなく **070・071 まで進行済み**だが、本件は既存 **ADR-062 の追補**として記録した（decisions.md 同期済み）。
>
> **実装済み（2026-07-02）**: §1〜§4 のとおり backend（schema/migration `0033`・repo・service・注入経路・Tool・router・persister）＋frontend（`/cards` 銘柄欄＋一覧表示・銘柄詳細「この銘柄のノート」導線）を実装。ATDD＝`tests/test_stock_scoped_cards.py`（§4 の 13 項目）。ゲート＝backend pytest **1037 passed**・pyright 0・自分の変更ファイルは ruff/format green（既存 baseline の E501 は untouched な 0023/0024/0032 のみで本件と無関係）・frontend tsc/Biome green。
>
> **進め方**: memory「ATDD＋ADR 同期」に従い、§4 の受け入れテストを先に書いてから実装。設計変更は本ファイル→`docs/decisions.md`（ADR-062 追補）→各 docs へ同期。

---

## 0. 背景 ─ ドシエと知識ノートの切り分け

「銘柄特有のアノマリー」を**ドシエではなく知識ノート側**に置く、が結論。軸は **事実 vs 解釈 / 揮発 vs 蓄積**。

| | ドシエ（`stock_dossiers`） | 知識ノート（`knowledge_cards`） |
|---|---|---|
| 持つもの | 「今この銘柄がどうか」の調査要約（**事実の要約**） | 「この知識をどう使うか」の**解釈・規律・非自明な知見** |
| 鮮度 | 揮発的・**毎晩 `investigate_dossier` で上書き**の living document（ADR-020） | **安定資産・蓄積する** |
| 更新 | AI が自動上書き（cadence＝`interval_days`・ADR-033） | 人間が active 化して初めて効く（承認制・ADR-009） |
| 粒度 | 1 銘柄 1 行（`code` PK） | 知識 1 枚（**従来 `code` 列なし**＝本件で追加） |

- アノマリーは**時間に耐える解釈**であり日々の事実ではない → ドシエに書くと毎晩の再調査で流される。蓄積すべきものを揮発層に置くのは設計ミス。
- アノマリーは**本番助言を左右する重い知見** → 人間承認（active 化）を要する知識ノートのライフサイクルと合致。ドシエには承認の概念がない。
- 「複数銘柄共通」の大半は**構造的まとまり**（業種・テーマ）で、`knowledge_cards` に既にある `sector17_code`・`theme`・`level` で表現する。任意バスケットだけがスカラー `code` で持てないが、それはレアで複製 or 将来 join 昇格で対応（§5）。

---

## 1. 決定事項（設計ツリーの合意）

| # | 分岐 | 決定 |
|---|---|---|
| ① | カード↔銘柄の多重度 | **1 カード = 1 銘柄**（スカラー）。任意バスケットは複製 or 後から join 昇格 |
| ② | market 列 | **`market`＋`code` を両方持つ**（US-ready）。注入は当面 `code` 一致で引く（`FocusRef` 無改修） |
| ③ | chat 注入 | (1) `focus.code` の active ノートを**無条件注入**（`weight` 降順・上限 N）／(2) `code` 付きノートは**汎用の意味検索プールから除外**（他銘柄漏れ防止） |
| ④ | 夜 AI | **注目候補の `code` ぶんを決定論注入**（`{候補} ∩ {ノート持ち}` ＝実質数枚）。ADR-062「夜=ambient のみ」を小幅拡張（ADR-067 の直接注入精神と整合） |
| ⑤ | フォーム付与 | **`code` は明示フィールド＋実在検証**（LLM 抽出しない＝grounding 厳守・ADR-050/056）。銘柄詳細ページから `focus.code` プリフィル |
| ⑥ | `propose_card` | **`code?`/`market?` を追加**（会話の tool 文脈由来＝grounded・未知は drop＝ADR-052 同型）。draft→人間 active 化は不変（ADR-009） |
| ⑦ | level 整合 | **`code` あり ⟺ `level='stock'`**／`code` あり ⇒ **`always_inject` 禁止**（0 に矯正）／`CardUpdateIn` で `code`・`market` 編集可 |
| ⑧ | `search_cards` | **`code?`/`market?` 追加**。`code` 指定=その銘柄の active を exact-match 全返し（意味クエリ無視）／`code` 無し=非銘柄カードの意味検索（③(2) と対称） |
| ⑨ | migration/FE | **migration 0033**（`code`・`market` nullable＋`ix(market, code)`・backfill なし）／`/cards` に銘柄欄＋一覧表示、銘柄詳細に「この銘柄のノート」導線 |

**注入式（chat）**: `always_inject ∪ 完全一致(focus.code の全 active・weight 降順 cap) ∪ 意味検索top-K(code 無しのみ)` を `id` で dedup。

---

## 2. ADR-062 追補（ドラフト本文）

> **追補（銘柄粒度の知識軸・code スコープ）**
>
> **文脈**: 知識カードは `level`=stock/sector/market/general の構造タグを持つが、`level='stock'` を成立させる銘柄同定列がなく、個別銘柄特有の知見（アノマリー等）を厳密に紐づけられなかった。ドシエ（`stock_dossiers`）は living document で毎晩上書きされる**揮発的な事実の要約**であり、蓄積すべき**解釈的知見**の器としては不適（上書きで消える・承認の概念がない）。
>
> **決定**: `knowledge_cards` に `market`＋`code`（ともに nullable）を追加し、`level='stock'` を「1 カード = 1 銘柄」で成立させる。`code` の同一性は**常に決定論**で握る（フォームは明示欄＋実在検証、`propose_card` は会話の tool 文脈由来＝いずれも名前推測をせず＝ADR-050/056 の grounding 規律を踏襲）。注入は **exact-match を最優先**（chat は `focus.code` の全 active を無条件注入、夜は注目候補の `code` ぶんを決定論注入）。銘柄ノートは**汎用の意味検索プールから除外**し、他銘柄会話への漏れを防ぐ。active 化は従来どおり人間承認（ADR-009 不変）。
>
> **帰結**: 多銘柄共通の知見は既存の `sector17_code`/`theme`（構造的まとまり）で表現し、任意バスケットは複製 or 将来の join 昇格（`code IS NOT NULL` を `card_stocks` へ移送するロスレス移行）で対応する。ADR-062 の「夜 AI=ambient のみ」を、銘柄ノートに限り決定論注入する形で小幅拡張（ADR-067 の「候補を直接注入し AI に掘らせない」精神と整合）。`FocusRef` に `market` を通すのは将来（当面 `code` 一致で衝突しない＝JP 数字系 / US 英字系）。

---

## 3. 実装スコープ（触るファイル・被り注意）

- `db/schema.py` … `knowledge_cards` に `code`・`market` 列＋`ix(market, code)`。
- `db/repo/knowledge_cards.py` … `_CARD_COLS`/`_EDITABLE_COLS` に `code`・`market` 追加。`search_knowledge_cards` に `code`/`market` 引数＋「`code` なしだけ」モード。`insert_knowledge_card_tx` に引数追加。exact-match 取得関数（`list_active_cards_by_code` 等）を新設。
- `services/knowledge_cards.py` … `load_card_texts_for_injection(query, *, focus_code=None, extra_codes=None)` へ拡張（chat=focus_code / 夜=注目候補 codes）。汎用意味検索は `code` 無しに限定。
- `advisor/router.py` … chat が `req.context.focus.code` を注入に渡す。
- `advisor/nightly.py` … `build_notable_candidates` の code を集めて注入に渡す。
- `advisor/prompt_builder.py` … （必要なら）注入テキストの体裁のみ。`FocusRef`/`ScreenContext` は無改修。
- `advisor/tools/schemas.py` … `ProposeCardArgs` に `code?`/`market?`、`SearchCardsArgs` に `code?`/`market?`。
- `advisor/tools/handlers.py` … `propose_card`/`search_cards` ハンドラ（**ADR-071 が同ファイルを別関数で編集済み＝衝突なし**）。
- `routers/cards.py` … `CardCreateIn`/`CardUpdateIn`/`CardOut` に `code`/`market`。`POST /cards` で実在検証＋level='stock' 矯正＋always_inject 禁止。
- `advisor/card_triage.py`（`assist_card`）… `code` が明示済みなら level を 'stock' で上書き（推測させない）。
- `alembic/versions/0033_*.py` … 追加列＋index、backfill なし。
- frontend … `/cards` 追加フォームに「銘柄（任意）」欄＋一覧に code 表示、銘柄詳細（`DossierSection` 付近）に「この銘柄のノート」導線（`focus.code` プリフィル）、`lib/api` 型を 1:1 追随。
- docs … `docs/decisions.md`（ADR-062 追補）・`docs/advisor.md`（注入方針）・`docs/data-model.md`（列追加）・`docs/api.md`（`/cards` I/O・Tool 引数）。

---

## 4. 受け入れテスト項目（ATDD・先に書く）

**repo/service 層**
1. `search_knowledge_cards(code=X)` … その `code` の active のみ `weight` 降順で返る／embedding 有無を問わない。
2. `search_knowledge_cards`（`code` 指定なし・汎用意味検索）… **`code` 付きカードが結果に混ざらない**（除外の検証）。
3. `load_card_texts_for_injection(query, focus_code=X)` … `always_inject ∪ code一致(全active) ∪ 意味検索(code無しのみ)` を `id` で dedup／上限 N。

**注入経路**
4. chat：`focus.code=6920` のとき 6920 の active ノートが意味距離に関わらず全部入る。
5. chat：`focus.code=7203` のとき 6920 のノートは**入らない**（漏れ防止）。
6. 夜 AI：注目候補に 6920 が居て 6920 にノートがあれば注入される／候補に居ない銘柄のノートは入らない。

**付与経路**
7. `POST /cards`：`code` 付き → 実在すれば `level='stock'` で保存／**未知 code は 400**。
8. `POST /cards`：`code` 付き → `always_inject` は 0 に矯正（禁止）。
9. `propose_card(code=6920)`：draft 起票＋`ChatResponse.card_ids` に載る／未知 code は drop。
10. `assist_card`：`code` が明示済みなら level 判定を 'stock' で上書き（AI に code を推測させない）。

**整合・migration**
11. `CardUpdateIn` で `code`/`market` を付け替え・除去できる（除去で汎用プールへ戻る）。
12. migration 0033 適用で既存カードは `code=NULL` のまま（backfill 無し）を実 SQLite で確認。
13. `search_cards` Tool（min_phase=4）：`code` 指定で exact-match が返る。

---

## 5. 未決・留意点

- **任意バスケット**（業種でもテーマでもない恣意的な複数銘柄）は当面「カード複製」で凌ぐ。実運用で頻発したら `card_stocks(card_id, market, code)` を作り `code IS NOT NULL` を移送→`code` 列を落とす**ロスレス昇格**（スカラーは join の部分集合）。
- **`FocusRef` に `market` を通すのは将来**。当面は `code` 一致で衝突しない（JP=数字系 / US=英字系）。US ノートの厳密フィルタが要るときに frontend+API を改修。
- **夜 AI の code 注入は実質 JP**（注目候補は JP signals 起点）。US ノートは当面 chat/`search_cards` 経由が主。
- **上限 N（chat 完全一致・weight 降順）**は config つまみ（既存 `_INJECT_RETRIEVE_LIMIT=5` と同程度が叩き台）。1 銘柄に大量ノートは想定しないが保険。

---

## 6. ゲート方針（実装時）

- backend pytest green（§4 を新規追加）・ruff/format green・pyright は新規エラー 0。
- migration は実 SQLite で `upgrade head` 適用確認。
- frontend tsc/biome green（frontend 自動テストは導入しない＝testing-strategy）。
- 本番ビルドは走らせない（dev サーバで確認）。
