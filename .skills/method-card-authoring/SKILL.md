---
name: method-card-authoring
description: 新しいシグナル/手法（signal_type）を追加・改名するとき、または既存手法の解釈文脈を直すときに必ず使う。手法カード（advisor/method_cards/<signal_type>.md）＝リポジトリ所有・signal_type キー・get_method_card でオンデマンド注入の第4知識源の作法（frontmatter・厚さの目安・ドリフト検査・knowledge_cards との住み分け）を規定する（ADR-075）。
---

# 手法カード（method card）の作法

**手法カード**は、AI Advisor が signals/手法のスコアを解釈するための「何を測る・スコアの読み方・限界」を書いたリポジトリ所有の参照知識（ADR-075）。CORE／POLICY／knowledge_cards に続く第 4 の知識源。

計算そのものは持たない（計算は必ず `quant/*.py` のテスト済みコード＝ADR-016）。カードは「いつ・どう読むか」だけを書き、数値は `get_*` Tool の戻り値（事実）を使う（ADR-014）。

## 置き場とガバナンス（不変条件）

- 実体は **`backend/app/advisor/method_cards/<signal_type>.md`**（1 signal_type 1 ファイル・ファイル名＝signal_type）。起動時に `method_cards.py` ローダが dict へ読む。
- **アプリ/AI からは追加・編集できない**。手法追加は必ずコード変更（`quant/*.py` 等）を伴うので、カードも **同じ PR に git で入れ code review する**（CORE がリポジトリ markdown なのと同じ governance＝ADR-015）。`/cards` UI（knowledge_cards）とは別系統。
- **knowledge_cards（DB・UI・AI triage・retrieval）とは住み分ける**。手法の解釈＝method card（コード依存の正典）。市場文脈・外部メモ・ユーザー知識＝knowledge_cards。**手法解釈を knowledge_cards に入れない**（ADR-062 を ADR-075 がこの点だけ上書き）。
- **`knowledge_cards.linked_signal_type` は使わない**（ADR-075 で非推奨）。手法↔signal の対応は method card がファイル名で持つ。

## 注入モデル（skill 型 progressive disclosure）

- **メタは常時露出**＝frontmatter の `summary` が `get_method_card` Tool の description に自動で並ぶ（Claude Code skill の description が常に見えるのと同型）。
- **本文は遅延ロード**＝LLM が `get_method_card(signal_type)` を呼んだ時だけ本文を返す。**決定論的な自動注入はしない**（全カード常時注入で破綻した歴史を繰り返さない＝ADR-062）。
- だから `summary` は「この手法カードを引くべきか」を LLM が 1 行で判断できるように書く（発火のキー）。

## ファイルの形（frontmatter ＋ 本文）

```markdown
---
signal_type: <signals の signal_type と完全一致>
summary: <1 行。何の手法か＋読むべき場面。get_method_card のカタログに常時出る>
---
# <signal_type> — <手法の通称>

<1〜2 段落で手法の要旨（何をどう捉えるか）。独自手法は「なぜそう言えるか」の仮説も。>

## 何を測るか        （独自手法のみ。教科書手法は省略可）
- <入力→出力・対象（銘柄か業種か）など>

## スコアの読み方
- <score の意味（0..1 が何を表すか・絶対値か相対順位か）と payload の主要フィールド>

## 限界・注意
- <誤読しやすい点・提示専用か・鮮度依存・未検証事項など。ここが独自手法で最重要>

計算の真実は `quant/<...>.py`。（深掘りがあれば docs/methods/<...>.md を参照）
```

`signal_type` を書かない場合はファイル名（`<stem>`）が使われるが、**明示を推奨**（改名時の取り違え防止）。

## 厚さの目安（一律で用意し、厚さを変える）

- **教科書手法**（GC/RSI/モメンタム・出来高急増など、強い LLM が一般知識で解釈できるもの）＝**薄く**。要旨＋読み方＋「単独で結論しない」等の一言で足りる。
- **独自・非自明な手法**（自作モデル・論文実装・独自の合成指標など、説明が無いと誤解するもの）＝**厚く**。特に「限界・注意」を丁寧に（提示専用・未検証・鮮度依存・レジーム依存など）。

判断に迷う「これは常識か？」は**厚めに倒す**（切り分けの手間より誤解の害が大きい）。

## 追加手順（新しい signal_type を足すとき）

1. `quant/*.py` に手法（テスト済み純関数）を実装し、`signals` に `signal_type` を焼く（ADR-016）。
2. `backend/app/advisor/method_cards/<signal_type>.md` を上の形で書く。
3. ドリフト検査の基準集合（テストが持つ「既知 signal_type の集合」）に新 `signal_type` を足す。起動時/テストで「カードの書き忘れ（missing）・孤児カード（orphan）」を突き合わせる。
4. `get_method_card` は新カードを自動でカタログ露出・返却する（Tool 側の変更は不要）。
5. `docs/`（decisions.md の該当 ADR・advisor.md）を同期する。

## やってはいけないこと

- 手法の**計算式・閾値をカード本文に書いて LLM に計算させる**（計算は `quant/*.py`・ADR-014/016）。カードは解釈のみ。
- 手法解釈を **knowledge_cards（DB）や CORE に入れる**（正典はリポジトリの method card・普遍規律だけ CORE＝ADR-015/062/075）。
- 厚い散文を **`quant/*.py` の Python 文字列に埋め込む**（純関数モジュールを汚す・日本語長文の編集も痛い＝markdown に置く）。
- `signal_type` とファイル名を**食い違わせる**（ドリフト検査で弾かれる）。
