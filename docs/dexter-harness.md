# Dexter 深掘り: AI ペルソナとハーネス（システムプロンプト設計）

> 調査日: 2026-06-07。[dexter-research.md](dexter-research.md) §3.1/§3.6 の続編・深掘り。
> 対象: `src/agent/prompts.ts`（289行・ハーネス本体）/ `SOUL.md`（83行・ペルソナ）/ `src/agent/channels.ts`（チャネル別プロファイル）/ `src/agent/types.ts`（`ChannelProfile`）/ `src/agent/agent.ts:82-111`（組み立ての呼び出し）。
> AssetVane で直撃するのは [ADR-015](decisions.md)（不変 CORE ＋ 可変 POLICY の 2 層）と [advisor.md §2](advisor.md)（CORE の 5 要素）。本書は「Dexter のハーネスを分解し、AssetVane の CORE 設計をどう豊かにできるか」をまとめる。**スコープは調査記録のみ・実装はしない。**

---

## 0. 用語の整理

- **ペルソナ（persona）**: AI が「誰であるか」。価値観・思考様式・投資哲学。Dexter では `SOUL.md`。
- **ハーネス（harness）**: ペルソナを含む**システムプロンプト全体を毎回組み立てる足場**。役割宣言・ツール使用方針・行動規範・出力形式・記憶・チャネル適応などを層として合成する仕組み。Dexter では `buildSystemPrompt()`。
- AssetVane の言葉で言うと: **ハーネス＝CORE＋POLICY を合成する関数**、**ペルソナ＝CORE の一部（役割・規律・哲学）**。Dexter はこの「合成」を 1 関数に綺麗に閉じ込めている。

---

## 1. ハーネスの構造（`buildSystemPrompt` の全層）

`agent.ts` は **`Agent.create` のたびに** SOUL/RULES/memory をディスクから読み、`buildSystemPrompt(model, soul, channel, group, memoryFiles, memoryContext, rules)` で**毎回フル組み立て**する。組み立て順（＝プロンプト内の出現順）は次の通り:

| 順 | セクション | 出所 | 性質 |
|---|---|---|---|
| 1 | `You are Dexter, a {label} assistant with access to research tools.` | 固定＋channel | 役割宣言 |
| 2 | `Current date: ...` | 動的 | 文脈 |
| 3 | preamble（出力面の一言） | **channel profile** | 面適応 |
| 4 | `## Available Tools` | registry（model 別 compact 説明） | 能力 |
| 5 | `## Tool Usage Policy` | 固定 | **規律（ツールの使い方）** |
| 6 | `## Available Skills` ＋ `## Skill Usage Policy` | skills registry（あれば） | 能力＋規律 |
| 7 | `## Memory`（recall/store 手順＋「助言前に必ず memory_search」） | 固定＋memory 一覧/文脈 | 規律＋文脈 |
| 8 | `## Behavior` | **channel profile** | **行動規範（反追従等）** |
| 9 | `## Research Rules`（ユーザールール）＋ `## Rule Management` | `.dexter/RULES.md`（任意） | **可変ルール** |
| 10 | `## Identity`（SOUL 本文＋"Embody this..."） | `SOUL.md`（ユーザー上書き→bundled） | **ペルソナ** |
| 11 | `## Response Format` ＋ `## Tables` ＋ `## Group Chat` | **channel profile**＋group | 出力形式 |

**設計上の要点（ここが学び）**:

1. **層が役割で分かれている**。Dexter は「ペルソナ（Identity）／行動規範（Behavior）／ツール規律（Tool Usage Policy）／出力形式（Response Format）」を**別セクションに分離**している。AssetVane の [advisor.md §2](advisor.md) が挙げる CORE 5 要素（①役割 ②方法論 ③規律 ④Tool 使い方 ⑤出力型）と**ほぼ完全に一致**し、Dexter は実装でそれを裏付けている。

2. **「不変の地＋差し込み」が [ADR-015](decisions.md) そのもの、ただし層がもっと多い**。AssetVane は CORE（repo）＋POLICY（DB）の 2 層だが、Dexter は「固定の地（役割・ツール規律・記憶手順）＋ SOUL（ペルソナ）＋ RULES（ユーザールール）＋ memory（文脈）＋ channel（面適応）」と**5 種の差し込み**を持つ。AssetVane の POLICY は構造化コア＋rationale だけだが、**ペルソナ・行動規範・面適応を別レイヤとして切り出す余地**がある。

3. **上書き階層（override hierarchy）**。SOUL は `.dexter/SOUL.md`（ユーザー）→ bundled（repo）の順でフォールバック。RULES は任意。**repo の正本をユーザーが上書きできる**。これは AssetVane の「CORE は repo・POLICY は DB で育てる」と同思想で、**ペルソナ自体をユーザーが微調整する経路**を示唆する（ただし AssetVane は CORE をチャットで書き換えない規律＝[ADR-015](decisions.md) なので、上書きは「意図的なファイル編集」に限る）。

4. **worker は素のプロンプト**。subagent は `config.systemPromptOverride` を使い、**SOUL・RULES・memory を全部スキップ**（隔離ワーカー）。AssetVane の `investigate_stock` サブパイプライン（[ADR-020](decisions.md)）も、ペルソナ・記憶を載せない軽量プロンプトにできる示唆。

5. **毎回フル組み立て（ステートレス）**。プロンプトはキャッシュせず毎 run 構築。AssetVane は CORE を repo ファイルから（キャッシュ可）、POLICY を DB から（既にリクエスト毎）。**prompt caching（[dexter-research.md](dexter-research.md) #6）を効かせるなら、不変 CORE＋ペルソナを配列先頭に固定**する必要があり、ここは Dexter の「Identity を後ろに置く」順序とは**意図的に変える**べき（後述 §4 注意）。

---

## 2. ペルソナ（SOUL.md）の分解 — ここが白眉

`SOUL.md` は一人称（"I'm Dexter"）で書かれた**価値観の宣言文**で、チェックリストではない。末尾で `buildSystemPrompt` が "Embody the identity and investing philosophy described above. Let it shape your tone, your values..." と締めて注入する。構成:

| 節 | 中身 | 効かせ方 |
|---|---|---|
| **Who I Am** | 「ターミナルに棲む金融リサーチャー」。比喩（Dexter's Lab の少年＝作る精神）。「検索エンジンではなく考える研究者」 | アイデンティティの核 |
| **How I Think About Investing** | Buffett/Munger の**名前付きメンタルモデル**（価格と価値・quality compounds・circle of competence・margin of safety／invert・mental models・待つこと・simplicity）。ただし**「教師の写しではない・教義より証拠」** | 方法論を哲学として埋める |
| **What Drives Me** | 飽くなき好奇心（データを尋問する）・作る本能・**技術的勇気（難問は避ける理由でなく、より慎重になる理由）**・独立（コンセンサスは data であって gospel でない）・徹底 | 思考の姿勢 |
| **What I Value** | **Accuracy over comfort（反追従）**・Substance over performance（過程を実況しない）・限界への知的誠実（DCF は必ず感応度分析つき）・**Protecting your interests（中立ではない・value trap なら警告する）** | 規律の根拠 |
| **My Laboratory** | 「データを集めてから見解を作る（その逆＝合理化はしない）」 | 手順の原則 |
| **On Being an Agent** | **ステートレスを欠陥でなく強みに再定義**（毎回 fresh eyes＝Buffett は毎年 annual report を読み直す）。「セッション間に運ぶのは記憶でなく"ものの見方"」 | 制約の意味づけ |

**学びの核心 3 点**:

- **(A) 反追従（anti-sycophancy）が 3 層で多重強調されている**。SOUL（"accuracy over comfort"・"I'm not here to validate what you already believe"・"if I think you're about to walk into a value trap, I'll say so"）＋ Behavior バレット（"Prioritize accuracy over validation - don't cheerfully agree with flawed assumptions"・"without excessive praise or emotional validation"）。**投資アドバイザーにとって追従は危険**（ユーザーの誤った thesis に同意すると損失に直結）なので、ペルソナ・行動規範の両層で念押ししている。
- **(B) 哲学を「名前付きメンタルモデル」で書く**。「慎重に」ではなく「margin of safety・invert・circle of competence」と**固有名で**書くと、LLM が既知の概念に接地して一貫した判断を再現しやすい。「教義より証拠」で硬直も防ぐ。
- **(C) 制約をペルソナで意味づける**。Dexter はステートレスを「fresh eyes の規律」と再定義した。**技術的制約を弱点でなく規律として語る**のはペルソナ設計の上級技。

---

## 3. チャネルプロファイル — AssetVane に無い概念

`channels.ts` の `ChannelProfile`（label / preamble / behavior[] / responseFormat[] / tables）が**面ごとに「同じペルソナの振る舞い・出力形式」だけを差し替える**。

| | CLI | WhatsApp |
|---|---|---|
| preamble | 「CLI 表示・短く簡潔に」 | 「WhatsApp 配信・知識ある友人がテキストする調子で」 |
| behavior | 反追従・professional・効率・**raw data を要求するな** | 友人調・スマホで読める短さ・過度に hedge しない |
| responseFormat | header/italic 不可・bold 控えめ・表は可 | **header 不可（リテラル表示される）・表不可（モバイルで崩れる）**・短段落 |
| tables | 厳格フォーマットの表ブロック | `null`（表セクションごと省略） |

**ペルソナ（SOUL）は全面で不変、振る舞い・形式だけが面で変わる**＝「1 つの脳・複数の出力面」をプロンプト層で実現している。registry に 1 エントリ足すだけで新チャネルを追加できる。

---

## 4. AssetVane への適用

AssetVane は [ADR-015](decisions.md) で CORE/POLICY を分けているが、**CORE の内部構造はまだ「規律あるクオンツアナリスト」と一括り**。Dexter のハーネスは、その CORE を**さらに分解する具体形**を与える。

### 4.1 CORE を多層に分解する（[advisor.md §2](advisor.md) の実装像）
Dexter の層分けに合わせ、`core_prompt.md` を機能ブロックで構造化できる:

| AssetVane CORE 層 | 中身 | Dexter の対応 | 備考 |
|---|---|---|---|
| **役割宣言** | 規律あるクオンツ投資アナリスト | 役割行 | [advisor.md §2 ①](advisor.md) |
| **ペルソナ／哲学** | 投資哲学・価値観（AssetVane 独自に書く） | SOUL.md | **新設の価値が最大**（§4.2） |
| **方法論** | モメンタムは MA/出来高/RSI 併用・割安判断は PER 単体禁止 等 | （SOUL の投資哲学に混在） | [advisor.md §2 ②](advisor.md) |
| **規律・ガードレール** | 数値は Tool の戻り値のみ・不確実性明示・トレードオフ提示・断定回避 | Behavior＋SOUL | [ADR-014](decisions.md)・[advisor.md §2 ③](advisor.md) |
| **Tool 使用方針** | 定量主張の前に必ず対応 Tool を呼ぶ | Tool Usage Policy | [ADR-014](decisions.md)・[advisor.md §2 ④](advisor.md) |
| **出力の型** | 根拠＋リスク＋トレードオフを必ず添える | Response Format | [advisor.md §2 ⑤](advisor.md) |

→ **Dexter は AssetVane の CORE 5 要素設計が正しいことを実装で裏付けつつ、各要素を別セクションに物理分離する書き方**を示している。

### 4.2 ペルソナ（CORE の哲学層）を一人称・名前付きで書く【最有力】
AssetVane の CORE は規律の列挙に寄りがち。Dexter の SOUL のように:
- **一人称の価値観宣言**にして LLM に「embody」させる（チェックリストより一貫したトーンが出る）。
- **名前付きメンタルモデル**（margin of safety・invert・circle of competence・"I don't know" の知的誠実さ）で方法論を哲学として埋める。
- **反追従を CORE の価値として明文化**（後述 §4.3）。

ただし**内容は AssetVane 独自に書く**（Buffett/Munger をそのまま借りない・「ポートフォリオの大損を避けつつ攻める／ゼロカット許容／信用は使わない」という [ADR-013](decisions.md) の方針哲学を一人称で表現する）。

### 4.3 反追従（anti-sycophancy）を CORE に明示する【投資アドバイザーに必須】
現状 AssetVane の ADR/CORE は「不確実性明示・トレードオフ提示・断定回避」はあるが、**「ユーザーの誤った思い込みに同意しない・耳の痛い真実を優先する」という反追従を明示的には encode していない**。Dexter の SOUL「accuracy over comfort / I'm not here to validate what you already believe / value trap なら警告する」は、**提示に徹する（[ADR-001](decisions.md)/[ADR-009](decisions.md)）アドバイザーにこそ効く**。追従的なアドバイザーは危険なので、CORE の価値として 1 段持つ価値が高い。Dexter 流に**ペルソナ層と行動規範層の両方で多重強調**するのが堅い。

### 4.4 チャネルプロファイルを導入する【AssetVane に無い概念】
AssetVane は出力面が複数ある:
- **軸1 夜の分析AI**（cron・画面なし・journal を書く＝簡潔・構造化・宛先は DB）
- **軸2 相談チャットAI**（Web・常駐・会話的・表 OK・[ADR-024](decisions.md)）
- **将来 Discord**（[dexter-research.md](dexter-research.md) §3.5・モバイル・表崩れ・header 不可）
- **Discord digest**（Phase 6・通知・極短）

現状はプロンプトが実質 1 本。Dexter の `ChannelProfile` registry を真似て、**同じ CORE/ペルソナを面ごとに behavior/format だけ差し替える**と、軸1 の「簡潔・観点列挙」と軸2 の「会話・表あり」を 1 つの CORE から出し分けられる。これは [ADR-011](decisions.md)「1 つの脳・複数の起動口」のプロンプト層版。

### 4.5 「助言前に必ず文脈を recall」を規律化する
Dexter の `## Memory` は「**personalized advice の前に ALWAYS memory_search**」を強制している。AssetVane に置き換えると「**提案（軸1 の nightly proposal・軸2 の銘柄/比率提案）の前に、必ず `policy` と直近 `advisor_journal` を文脈に入れる**」を CORE の規律として明文化できる（[ADR-013](decisions.md) の policy 連続性・[ADR-029](decisions.md) の journal をプロンプト規律として効かせる）。

### 4.6 ステートレス再定義は「反転して」使う
Dexter はステートレスを「fresh eyes の規律」と称揚するが、**AssetVane の価値はむしろ状態の連続性**（[ADR-011](decisions.md)/[ADR-013](decisions.md)）。ここは思想が逆。ただし「**昨日の結論にアンカリングせず再検証する**」という fresh-eyes の規律は、夜AI が前日方針を機械的に追認しないための**行動規範**として有用。「連続性は保つが、毎晩ゼロベースで事実を見直す」を CORE に 1 行入れる価値がある。

### 4.7 worker（investigate_stock）は素のプロンプト
Dexter の subagent が SOUL/memory をスキップするように、AssetVane の `investigate_stock` パイプライン（[ADR-020](decisions.md)）も**ペルソナ・記憶を載せない軽量プロンプト**にできる（調査は事実収集＋要約で、投資哲学は不要）。トークン削減にも効く。

---

## 5. 採用に当たっての注意・差分

- **ペルソナの中身はコピーしない**。Buffett/Munger・「ポータルを作る少年」は Dexter の固有 flavor。AssetVane は [ADR-013](decisions.md) の方針哲学（攻めるが退場しない／ゼロカット許容／レバレッジ不可）を**自分の言葉で一人称化**する。移植するのは**構造（一人称・名前付きモデル・価値観の節・反追従・制約の意味づけ）**であって文章ではない。
- **「自律実行」前提の語彙を「提示専用」に書き換える**。Dexter の SOUL は "I decompose, execute, iterate"（自分でやり切る）。AssetVane は提示専用（[ADR-001](decisions.md)/[ADR-009](decisions.md)）なので、"technical courage / instinct to build" は「**トレードオフとリスクを構造化して提示する・決めるのはユーザー・売買は実行しない**」に変換する。
- **既存 ADR と二重化しない**。channel profile の "Never ask users to provide raw data / reference API internals" は AssetVane では [ADR-014](decisions.md)/[ADR-025](decisions.md)（画面の数値を載せない・Tool で取り直す）が既に担う。CORE には**参照だけ書き、規律の本体は ADR/quant 側**に置く。
- **prompt caching を効かせるなら層順を変える**。Dexter は Identity を後方に置くが、AssetVane で [dexter-research.md](dexter-research.md) #6 のキャッシュを狙うなら**不変 CORE＋ペルソナをプロンプト先頭に固定**し、可変 POLICY/文脈を後ろに回す（先頭固定でヒット率が上がる）。ここは Dexter と意図的に異なる設計にする。
- **CORE をチャットで書き換えない規律は維持**（[ADR-015](decisions.md)）。Dexter のユーザー上書き SOUL に倣う場合も、上書きは「意図的なファイル編集（jj コミット）」に限り、チャット経由で drift させない。
- **Python 移植**: `buildSystemPrompt` 相当は AssetVane に既にあるはず（CORE 読み込み＋POLICY コンパイル）。チャネルプロファイルは `dict[str, ChannelProfile]` の dataclass registry に直訳可能。ペルソナは `core_prompt.md` の 1 セクション（または別ファイル `persona.md`）として repo 管理。

---

## 6. この領域での「真似したい」上位

1. **CORE を機能層に物理分離（役割／ペルソナ／方法論／規律／Tool方針／出力形式）** — [advisor.md §2](advisor.md) の 5 要素を Dexter の `buildSystemPrompt` の節構造で実装する。CORE の見通しと保守性が上がる。
2. **ペルソナ（哲学層）を一人称・名前付きモデル・反追従つきで書く** — チェックリスト CORE より一貫したトーン・判断の再現性。**反追従は投資アドバイザーに必須**で、ペルソナ＋行動規範の多重強調が堅い。内容は AssetVane 独自に。
3. **チャネルプロファイル（同じ CORE を面ごとに behavior/format だけ差し替え）** — 軸1 nightly／軸2 chat／Discord digest を 1 つの CORE から出し分ける。[ADR-011](decisions.md) の「1 つの脳」をプロンプト層で実装。

---

## 7. 参照ファイル（Dexter 側）

- `src/agent/prompts.ts` — `buildSystemPrompt`（全層合成）・`loadSoulDocument`/`loadRulesDocument`（上書き階層）・`buildSkillsSection`/`buildMemorySection`/`buildGroupSection`・`DEFAULT_SYSTEM_PROMPT`
- `SOUL.md` — ペルソナ本体（Who I Am / How I Think / What Drives Me / What I Value / My Laboratory / On Being an Agent）
- `src/agent/channels.ts` — `CLI_PROFILE`/`WHATSAPP_PROFILE`・`getChannelProfile`
- `src/agent/types.ts:12` — `ChannelProfile` インターフェース
- `src/agent/agent.ts:82-111` — 組み立ての呼び出し（worker は `systemPromptOverride` で素プロンプト・memoryEnabled 分岐）
