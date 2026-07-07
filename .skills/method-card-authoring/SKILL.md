---
name: method-card-authoring
description: 新しいシグナル/手法（signal_type）や signal を持たない screen 戦略（kind:strategy）を追加・改名するとき、または既存手法の解釈文脈を直すときに必ず使う。手法カード（advisor/method_cards/<key>.md）＝リポジトリ所有・get_method_card でオンデマンド注入の第4知識源の作法（kind=signal/strategy・frontmatter・厚さの目安・ドリフト検査・knowledge_cards との住み分け）を規定する（ADR-075・ADR-079）。
---

# 手法カード（method card）の作法

**手法カード**は、AI Advisor が signals/手法のスコアを解釈するための「何を測る・スコアの読み方・限界」を書いたリポジトリ所有の参照知識（ADR-075）。CORE／POLICY／knowledge_cards に続く第 4 の知識源。

計算そのものは持たない（計算は必ず `quant/*.py` のテスト済みコード＝ADR-016）。カードは「いつ・どう読むか」だけを書き、数値は `get_*` Tool の戻り値（事実）を使う（ADR-014）。

## 置き場とガバナンス（不変条件）

- 実体は **`backend/app/advisor/method_cards/<key>.md`**（1 手法 1 ファイル・signal 種はファイル名＝signal_type・strategy 種はスラッグ）。起動時に `method_cards.py` ローダが dict へ読む。
- **アプリ/AI からは追加・編集できない**。手法追加は必ずコード変更（`quant/*.py` 等）を伴うので、カードも **同じ PR に git で入れ code review する**（CORE がリポジトリ markdown なのと同じ governance＝ADR-015）。`/cards` UI（knowledge_cards）とは別系統。
- **knowledge_cards（DB・UI・AI triage・retrieval）とは住み分ける**。編集不要の確立手法（signal でも screen でも）＝method card（コード依存の正典）。市場文脈・外部メモ・ユーザー知識（UI で育てる）＝knowledge_cards。**手法解釈を knowledge_cards に入れない**（ADR-062 を ADR-075/079 がこの点だけ上書き）。
- **手法↔signal の対応は method card がファイル名で持つ**（旧 `knowledge_cards.linked_signal_type` は ADR-075 で非推奨化し 0035 で DROP 済み・別カタログ表も作らない）。

## 注入モデル（skill 型 progressive disclosure）

- **メタは常時露出**＝frontmatter の `summary` が `get_method_card` Tool の description に自動で並ぶ（Claude Code skill の description が常に見えるのと同型）。
- **本文は遅延ロード**＝LLM が `get_method_card(signal_type)` を呼んだ時だけ本文を返す。**決定論的な自動注入はしない**（全カード常時注入で破綻した歴史を繰り返さない＝ADR-062）。
- だから `summary` は「この手法カードを引くべきか」を LLM が 1 行で判断できるように書く（発火のキー）。

## kind は 2 種（signal / strategy・ADR-079）

手法カードは **2 種**ある。frontmatter の `kind` で見分ける（既定は `signal`）。

- **`kind: signal`（既定）**＝毎晩 `signals` に焼く signal_type の解釈（momentum/lead_lag 等）。ファイル名＝`signal_type` で `signals.signal_type` と 1:1。**ドリフト検査（orphan/missing）の対象**。AI は `signals` で見た signal を解釈する前に引く。
- **`kind: strategy`**＝`signals` を持たない手法（例: 清原式ネットキャッシュの screen 運用＝`net_cash_value`）。ファイル名＝手法スラッグで signal と 1:1 対応しないため**ドリフト検査の対象外**。AI は能動的に screen/Tool を使う前に引く。計算は `quant`（例: `quant.net_cash`）、絞り込みは既存 Tool（例: `screen_valuation` の `net_cash_ratio_min`）＝カードは「いつ・どの閾値で・なぜ」を書く。

「アプリ組み込みで編集不要の確立手法（signal でも screen でも）」は method_cards（正典）。UI で育てる知識（市場文脈・外部メモ）は knowledge_cards。この住み分けは「誰が所有し編集するか」で決まる（ADR-079）。

## ファイルの形（frontmatter ＋ 本文）

```markdown
---
kind: signal                       # 省略時 signal。screen 手法など signal を持たないものは strategy
signal_type: <signals の signal_type と完全一致>   # strategy はファイル名スラッグ（この行は省略可）
summary: <1 行。何の手法か＋読むべき場面。get_method_card のカタログに常時出る>
native_horizon: <この手法のエッジが効く時間軸。short/medium/long/day＋補足。ADR-091>
---
# <signal_type / スラッグ> — <手法の通称>

<1〜2 段落で手法の要旨（何をどう捉えるか）。独自手法は「なぜそう言えるか」の仮説も。>

## 何を測るか        （独自手法のみ。教科書手法は省略可）
- <入力→出力・対象（銘柄か業種か）など>

## スコアの読み方 / いつ・どう使うか
- signal＝score の意味（0..1 が何を表すか）と payload の主要フィールド。
- strategy＝どの Tool をどの閾値で呼ぶか（例: screen_valuation を net_cash_ratio_min≥1 で）。

## 限界・注意
- <誤読しやすい点・提示専用か・鮮度依存・未検証事項など。ここが独自手法で最重要>

計算の真実は `quant/<...>.py`。（深掘りがあれば docs/methods/<...>.md を参照）
```

`signal_type` を書かない場合はファイル名（`<stem>`）が使われるが、**signal 種は明示を推奨**（改名時の取り違え防止）。strategy 種はスラッグ＝ファイル名で足りる。

**`native_horizon`（ADR-091）** ＝この手法のエッジが実る想定時間軸。`get_method_card` のカタログに `（時間軸: …）` として常時露出し、advisor が「相談の時間軸に手法を合わせる」ための選別材料になる（例: 1 ヶ月狙いに翌営業日の `lead_lag` や年単位の `net_cash_value` を主根拠にしない）。値は投資家ホライズンの canonical（`short`/`medium`/`long`＝提案・採点と共通語彙）を基に、`day`（翌営業日サイン等・保有ホライズンでない）や `short〜medium` の範囲・短い補足を自由文字列で添える。全カード必須（未記載はカタログに時間軸が出ず選別できない）。

## 厚さの目安（一律で用意し、厚さを変える）

- **教科書手法**（GC/RSI/モメンタム・出来高急増など、強い LLM が一般知識で解釈できるもの）＝**薄く**。要旨＋読み方＋「単独で結論しない」等の一言で足りる。
- **独自・非自明な手法**（自作モデル・論文実装・独自の合成指標など、説明が無いと誤解するもの）＝**厚く**。特に「限界・注意」を丁寧に（提示専用・未検証・鮮度依存・レジーム依存など）。

判断に迷う「これは常識か？」は**厚めに倒す**（切り分けの手間より誤解の害が大きい）。

## 追加手順（signal 種＝新しい signal_type を足すとき）

1. `quant/*.py` に手法（テスト済み純関数）を実装し、`signals` に `signal_type` を焼く（ADR-016）。
2. `backend/app/advisor/method_cards/<signal_type>.md` を上の形で書く（`kind` は省略＝signal）。
3. ドリフト検査の基準集合（テストが持つ「既知 signal_type の集合」）に新 `signal_type` を足す。起動時/テストで「カードの書き忘れ（missing）・孤児カード（orphan）」を突き合わせる。
4. `get_method_card` は新カードを自動でカタログ露出・返却する（Tool 側の変更は不要）。
5. `docs/`（decisions.md の該当 ADR・advisor.md）を同期する。

## 追加手順（strategy 種＝signal を持たない screen 手法を足すとき・ADR-079）

1. `quant/*.py` に事実の計算純関数を足す（例: `net_cash`）。既存 Tool（例: `screen_valuation`）に絞り込み引数を生やす（signal は焼かない）。
2. `backend/app/advisor/method_cards/<slug>.md` を `kind: strategy` で書く（`signal_type` 行は不要）。本文に「いつ・どの Tool を・どの閾値で・なぜ」を書く（計算式で LLM に計算させない）。
3. ドリフト検査は **strategy を対象外にする**（`validate_method_cards` は signal 種のみ突合＝基準集合に strategy スラッグを足さない）。
4. `get_method_card` は自動でカタログ露出（`[strategy]` タグ付き）。必要なら関連 Tool の description に `get_method_card('<slug>')` への橋渡しを 1 文足す。
5. `docs/`（decisions.md の該当 ADR・advisor.md）を同期する。

## やってはいけないこと

- 手法の**計算式・閾値をカード本文に書いて LLM に計算させる**（計算は `quant/*.py`・ADR-014/016）。カードは解釈のみ。
- 手法解釈を **knowledge_cards（DB）や CORE に入れる**（正典はリポジトリの method card・普遍規律だけ CORE＝ADR-015/062/075）。
- 厚い散文を **`quant/*.py` の Python 文字列に埋め込む**（純関数モジュールを汚す・日本語長文の編集も痛い＝markdown に置く）。
- signal 種で `signal_type` とファイル名を**食い違わせる**（ドリフト検査で弾かれる）。
- strategy 種を**ドリフト検査の既知集合に足す**（signal 扱いされ missing 誤検出になる）。strategy は対象外が正。
