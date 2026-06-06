# J-Quants API（V2）

AssetVane が日本株・ETF・財務データを取得する API。

> 📌 **スコープ**: J-Quants は**日本株・日本 ETF 専用**。米国株・主要指数（S&P500 等）・為替（USD/JPY）は J-Quants の範囲外で、別データソース（`IndexAdapter` / `UsEquityAdapter` / `FxAdapter` / `NewsAdapter`）から取得する。米国対応は後期（[roadmap.md Phase 7](roadmap.md)）で、当面は日本株が主役（[decisions.md ADR-010](decisions.md)）。なお**適時開示（TDnet）は J-Quants の有料アドオン**で、課金後に利用する。

> ⚠️ **重要**: J-Quants は **2026 年 6 月 1 日に V1 API が提供終了**し、現在は **V2 のみ**が稼働する。認証は旧来の「トークン 2 段階方式」から **API キー方式（`x-api-key`）** に変更された。ネット上の記事・サンプルの多くは旧 V1（`/v1/...`・トークン 2 段）なので**そのままでは動かない**。本ドキュメントは **V2 基準**で記述する。
>
> 本ページの数値・仕様は調査時点（2026 年 6 月）のもの。末尾の「要再確認リスト」の項目は実装前に公式で再確認すること。

---

## 1. 料金プラン

| プラン | 月額(税込) | 株価遅延 | 格納期間 | 財務データ | レート上限 |
|---|---|---|---|---|---|
| **Free** | ¥0 | **12 週間遅延** | 約 2 年分 | 財務要約あり | 5 req/分 |
| **Light** | ¥1,650 | 遅延なし | 5 年 | 財務要約あり | 60 req/分 |
| **Standard** | ¥3,300 | 遅延なし | 10 年 | 財務要約あり | 120 req/分 |
| **Premium** | ¥16,500 | 遅延なし | 20 年（2008/5/7〜） | 財務要約あり | 500 req/分 |

- **AssetVane の方針**: 開発は **Free** で行い、短期機能を実運用する段階で **Light** 以上へ（[decisions.md ADR-008](decisions.md)）。
- アドオン（2026/1/19 追加）: 株価**分足・Tick** および **CSV 形式**提供。分足/Tick は Light 以上で月額 ¥5,500、格納期間は 2 年前まで。AssetVane は日足のみ使用するため**不要**。

---

## 2. 認証（V2）

- **方式**: API キー方式（1 段階）。ダッシュボードの「設定 » API キー」で発行したキーを HTTP ヘッダー `x-api-key` に付けてリクエストする。
- **旧 V1 の 2 段階方式（メール/PW → リフレッシュトークン → ID トークン）は廃止**。よって**夜間バッチでのトークン更新自動化は不要**になった。
- API キーは `.env` で渡す（[.env.example](../.env.example)）。

### リクエスト例

```bash
curl -G https://api.jquants.com/v2/equities/bars/daily \
  -H "x-api-key: ${JQUANTS_API_KEY}" \
  -d code="86970" \
  -d date="20240104"
```

---

## 3. 主要エンドポイント

ベース URL: `https://api.jquants.com`

| データ | V2 パス（現行） | AssetVane テーブル | 旧 V1（終了済・参考） |
|---|---|---|---|
| 上場銘柄一覧 | `/v2/equities/master` | `stocks` | `/v1/listed/info` |
| 日次株価四本値 | `/v2/equities/bars/daily` | `daily_quotes` | `/v1/prices/daily_quotes` |
| 財務情報 | `/v2/fins/summary` | `financials` | `/v1/fins/statements` |

---

## 4. レート制限

- 超過時は `429 Too Many Requests`。著しく継続超過すると約 5 分間アクセスがブロックされ全リクエストが失敗する。
- Free は **5 req/分**と厳しい。**銘柄ごとに 1 リクエストで全銘柄（約4000）を巡回すると初回バックフィルが理論上 13 時間超**になる。
- 対策: 株価四本値 API は**日付指定で「その日の全銘柄」を一括取得できる**。✅ **実機確認済み（2026-06）**: `/v2/equities/bars/daily?date=20251215` に `code` を渡さず叩くと、その日の東証全銘柄 **4428 行（1 銘柄 1 行・ETF/REIT 含む）** が 1 リクエスト（＋ページング）で返った。これを使い「日付ループ（営業日 × 1 リクエスト）」でバックフィルすれば、**全銘柄でも 2 年で約 500 リクエスト**に激減する（銘柄ループの 13 時間超に対し）。実装は `JQuantsAdapter.fetch_daily_quotes_by_date(date)`（Phase 1 の初回バックフィルの入口）。
- ⚠️ **本番投入の実走で判明（2026-06-04・Phase 1 全銘柄バックフィル）**:
  - **スロットルは 16 秒以上にする**。13 秒（4.6 req/分）は 60 秒の窓境界で 5 req に達し、4 営業日処理後に約 5 分ブロックを誘発して `fetch_quotes` が死んだ。**16 秒なら任意の 60 秒窓で最大 4 req** に収まり確実に下回る。間隔は **env のプラン名 `JQUANTS_PLAN`（`free`/`light`）から決まる**＝`free`→16s / `light`→1s（秒数はコードの `adapters/jquants.py` の `_PLAN_INTERVALS` が持ち、env で秒数をお守りしない・ADR-008）。V2 にプランを返す API は無い（§1 のレート表は手動参照）ので env でプラン名だけ指定する。
  - **429 リトライは約 5 分ブロックを跨げる長さにする**。旧設定（最大 16 秒バックオフ）では乗り切れなかった。現在は合計待機が約 6 分（2+4+8+16+32+64+120+120 秒・`_MAX_RETRIES=8`・上限 120 秒）に達するまで耐えてからブロックを諦める（`adapters/jquants.py`・ADR-018）。
  - **スロットル時刻はプロセス共有（クラス変数）**。夜間バッチはジョブごとにアダプタを作り直すため、インスタンス変数だと `sync_master → fetch_quotes` の境界で 2 連続バーストが出てブロックを誘発する。
  - これらにより **2 年フルバックフィルの所要は約 133 分（16 秒 × 約 500 営業日）**。Free 遅延の都合で末尾 ~12 週（約 60 営業日）は空レスを吸収しつつ進む。
- 上限はシステム状況により調整される可能性あり（公式注記）。

---

## 5. ETF の取得可否

- 日次株価 API は「**東証上場の全銘柄**」を配信対象とし、**ETF・REIT も含む**。
- よって **TOPIX-17 業種別 ETF（1617〜1633 等）の日足は Free でも取得可能**と判断（Phase 7 リードラグ戦略の日本側入力に使用）。
- ETF/REIT は 33 業種コードに該当しないため、銘柄区分上は業種コードが特殊値になるが、株価データ自体は普通株と同じ日足 API で取れる。
- 個別銘柄単位での提供は実装時に実機で 1 件確認すること（下記要再確認）。

---

## 6. 要再確認リスト（実装前にチェック）

調査で確定しきれず、コードを書く前に公式・実機で確認すべき項目。

1. **API キーの有効期限**: 「トークンと違い API キーに有効期限はない」旨の記述があるが、一次ソースでの確定文言は裏取り未了。
2. **V2 の財務詳細（BS/PL/CF）の提供範囲**: V2 では `/v2/fins/summary` に統一。V1 で Premium 限定だった詳細財務が V2 で別エンドポイントとして残るか不明。
3. **個別 ETF（1617〜1633）の名指し提供**: 「全銘柄対象」記述からは取得可だが、実機で 1 件確認推奨。なお日付一括取得（下記✅）には ETF/REIT も含まれることは確認済み。
   - ✅ **日付一括取得の可否（最重要）は確認済み（2026-06）**: `bars/daily` は `code` 無し・`date` のみで全銘柄が返る（§4 参照）。Phase 1 のレート制限対策が成立する。
4. **各エンドポイントの実レスポンスのフィールド名**: `data-model.md` の列名は設計案。実際の JSON キー名は実機で確認して合わせる。
   - ✅ **確認済み（2026-06）**: `master` と `bars/daily`。**V2 は略記キー**（`O/H/L/C/Vo/Va/AdjC/AdjFactor`、`CoName/S33/S17/Mkt` 等）でエンベロープは `{"data":[...]}`（`pagination_key` 付き）。対応表は [data-model.md](data-model.md) の各テーブル節。**ネットの V1 記事はフルネーム（`Open/CompanyName`…）なので流用不可**。
   - ✅ **確認済み（2026-06・ADR-031）**: `fins/summary`（財務）。短縮キー `EPS/BPS/Sales/OP/NP/DiscDate/CurPerType`、配当 `FDivAnn`(予想年間)/`DivAnn`(実績)、株数 `ShOutFY`(発行済)/`TrShFY`(自己株)。値は**文字列**・N/A は**空文字**（`_to_float` で None 化）。**BPS は通期(FY)行のみ・四半期 EPS は累計**。
5. **`/v2/equities/master` の `code` 無し全件取得可否**: 銘柄マスタを `code` を渡さず叩いて全上場銘柄を一括で取れるか（`bars/daily` の日付一括と同様に成立するか）を実機確認する。不可なら `bars/daily` で得た `code` 集合から不足分を補完する（Phase 1・`_arbitration.md` L-5）。
6. **V2 の取引日カレンダー API の有無**: 営業日（取引日）の一覧を返す V2 エンドポイントがあるか。無ければ営業日判定は「曜日で土日除外＋祝日は空レスポンスで吸収」で代替する（Phase 1・`_arbitration.md` L-3）。
7. **V2 の主要指数 API（TOPIX / 日経平均）の有無**: TOPIX・日経平均等の指数水準を返す V2 エンドポイントがあるか。無ければ `IndexAdapter`（Stooq 既定）で取得する（Phase 2・`index_quotes`・`_arbitration.md` L-10）。
8. ~~**V2 財務（statements）エンドポイント**~~ ✅ **解消（2026-06・ADR-031）**: 財務は **`/v2/fins/summary`**（`/v2/fins/statements` は **403**）。売上/営業利益/純利益/EPS/BPS・開示日・会計期間に加え、**年間配当（FDivAnn/DivAnn）・発行済株式数（ShOutFY）・自己株式（TrShFY）も summary で揃う**ことを実機確認（→ 時価総額・配当利回りも J-Quants 単独で導出可能）。`fetch_financials` は **by-date 一括**（その日開示の全銘柄）も成立。フィールド対応は `adapters/jquants.py` の `_normalize_financial`。

---

## 7. 出典

- 料金: <https://jpx-jquants.com/>
- プラン別 API / 期間: <https://jpx-jquants.com/ja/spec/data-spec>
- V1→V2 変更・パス・認証: <https://jpx-jquants.com/spec/migration-v1-v2>
- V2 認証（x-api-key）: <https://jpx-jquants.com/ja/spec/quickstart>
- レート制限: <https://jpx-jquants.com/en/spec/rate-limits>
- 全銘柄対象（ETF 含む）: <https://jpx.gitbook.io/j-quants-ja/api-reference/daily_quotes>
- CSV・分足/Tick アドオン: <https://www.jpx.co.jp/corporate/news/news-releases/6020/20260119.html>
- LINE Notify 終了（2025/3/31）: <https://developers.line.biz/ja/news/2025/04/01/line-notify/>
