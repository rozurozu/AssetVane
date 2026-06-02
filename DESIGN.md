---
version: "1.0"
name: AssetVane-dashboard
description: "データ密度を最優先にした、単一ユーザー向け投資ダッシュボードのデザインシステム。Framer 由来の near-black ダークキャンバスと surface lift・単一アクセント青の『色』だけを継承し、配置・余白・コンポーネントはダッシュボード用にゼロから組み直した。カードの過剰な余白を排し、罫線区切りと詰めた行高で情報を高密度に並べる。損益は緑=上昇/赤=下落のグローバル標準。数値は Inter Variable の tabular figures で桁を揃える。"

colors:
  # --- 基盤サーフェス（Framer 継承）---
  canvas: "#090909"          # 最下層の背景。アプリ全体の地
  surface-1: "#141414"       # 1段上。サイドバー・カード・テーブルヘッダ
  surface-2: "#1c1c1c"       # 2段上。選択行・hover・featured カード
  surface-3: "#242424"       # 3段上。popover・tooltip・modal
  hairline: "#262626"        # 1px 罫線（区切りの主役）
  hairline-soft: "#1a1a1a"   # さらに弱い罫線（zebra 行・微細な区切り）

  # --- テキスト ---
  ink: "#f2f2f2"             # 本文・見出し（純白 #fff は眩しいので僅かに落とす）
  ink-muted: "#9a9a9a"       # 副次テキスト・ラベル・メタ
  ink-subtle: "#5f5f5f"      # プレースホルダ・無効・最弱の補助

  # --- 単一アクセント（Framer 継承。signal 専用、面塗りしない）---
  accent: "#0099ff"          # リンク・フォーカスリング・選択・アクティブ指標
  accent-weak: "#0d2c44"     # accent の薄い背景（選択タブ地・focus halo）

  # --- 損益・方向（緑=上昇 / 赤=下落）---
  up: "#22c55e"              # 上昇・利益・買い
  up-weak: "#0f2e1c"         # up の薄背景（バッジ・行ハイライト）
  down: "#ef4444"            # 下落・損失・売り
  down-weak: "#3a1717"       # down の薄背景
  flat: "#9a9a9a"            # 変化なし（= ink-muted）

  # --- セマンティック（UI 状態。損益とは用途を分ける）---
  success: "#22c55e"
  warning: "#f59e0b"
  danger: "#ef4444"
  info: "#0099ff"

  # --- チャート系列色（Framer の gradient 群を spotlight card から転用）---
  chart-1: "#0099ff"         # accent 青
  chart-2: "#6a4cf5"         # violet
  chart-3: "#d44df0"         # magenta
  chart-4: "#ff7a3d"         # orange
  chart-5: "#22c55e"         # green
  chart-6: "#2dd4bf"         # teal
  chart-7: "#ff5577"         # coral
  chart-8: "#f5c518"         # amber

typography:
  # 巨大 display（110px/-5.5px 等）はダッシュボードでは使わないため廃止。
  # 最大でも 28px。階層はサイズ＋色（ink/ink-muted）で付け、太さの振り幅は狭く保つ。
  display:
    fontFamily: Inter Variable
    fontSize: 28px
    fontWeight: 600
    lineHeight: 1.15
    letterSpacing: -0.6px
    fontFeature: cv11
  title:
    fontFamily: Inter Variable
    fontSize: 20px
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: -0.4px
    fontFeature: cv11
  heading:
    fontFamily: Inter Variable
    fontSize: 16px
    fontWeight: 600
    lineHeight: 1.25
    letterSpacing: -0.2px
    fontFeature: cv11
  subhead:
    fontFamily: Inter Variable
    fontSize: 14px
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: -0.1px
    fontFeature: cv11
  body:
    fontFamily: Inter Variable
    fontSize: 14px
    fontWeight: 400
    lineHeight: 1.45
    letterSpacing: -0.1px
    fontFeature: cv11
  body-sm:
    fontFamily: Inter Variable
    fontSize: 13px
    fontWeight: 400
    lineHeight: 1.4
    letterSpacing: -0.08px
    fontFeature: cv11
  label:
    fontFamily: Inter Variable
    fontSize: 12px
    fontWeight: 500
    lineHeight: 1.2
    letterSpacing: 0.1px
    fontFeature: cv11
  caption:
    fontFamily: Inter Variable
    fontSize: 11px
    fontWeight: 500
    lineHeight: 1.2
    letterSpacing: 0.2px
    textTransform: uppercase
    fontFeature: cv11
  # 数値専用。価格・損益・比率・株数は必ず tabular figures で桁を揃える。
  numeric:
    fontFamily: Inter Variable
    fontSize: 14px
    fontWeight: 500
    lineHeight: 1.2
    letterSpacing: 0px
    fontFeature: tnum
  numeric-lg:
    fontFamily: Inter Variable
    fontSize: 22px
    fontWeight: 600
    lineHeight: 1.1
    letterSpacing: -0.2px
    fontFeature: tnum
  button:
    fontFamily: Inter Variable
    fontSize: 13px
    fontWeight: 500
    lineHeight: 1.0
    letterSpacing: -0.08px
    fontFeature: cv11

# 角丸は控えめ。pill（100px）はダッシュボードでは使わない（マーケ専用だったため廃止）。
rounded:
  none: 0px
  sm: 4px
  md: 6px
  lg: 8px
  xl: 12px
  full: 9999px

# 4px ベースの詰めたスケール。Framer の 5/10/15/20/30/96 系は余白過多なので破棄。
# section も 32px 程度に抑え、画面の縦を情報で埋める。
spacing:
  none: 0px
  px: 1px
  xxs: 2px
  xs: 4px
  sm: 6px
  md: 8px
  lg: 12px
  xl: 16px
  xxl: 24px
  section: 32px

components:
  # --- アプリシェル ---
  app-shell:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.none}"
  sidebar:
    backgroundColor: "{colors.surface-1}"
    textColor: "{colors.ink-muted}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.none}"
    width: 220px
  topbar:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.ink}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.none}"
    height: 48px
  nav-item:
    backgroundColor: "{colors.surface-1}"
    textColor: "{colors.ink-muted}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.md}"
    padding: 6px 10px
  nav-item-active:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.ink}"
    typography: "{typography.subhead}"
    rounded: "{rounded.md}"
    padding: 6px 10px

  # --- カード（余白控えめ。情報の箱であって余白の見せ場ではない）---
  card:
    backgroundColor: "{colors.surface-1}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.lg}"
    padding: 12px
  card-header:
    backgroundColor: "{colors.surface-1}"
    textColor: "{colors.ink}"
    typography: "{typography.subhead}"
    rounded: "{rounded.none}"
    padding: 8px 12px
  stat-card:
    backgroundColor: "{colors.surface-1}"
    textColor: "{colors.ink}"
    typography: "{typography.numeric-lg}"
    rounded: "{rounded.lg}"
    padding: 12px

  # --- データテーブル（密度の主戦場。行高を詰め、罫線で区切る）---
  table-header:
    backgroundColor: "{colors.surface-1}"
    textColor: "{colors.ink-muted}"
    typography: "{typography.caption}"
    rounded: "{rounded.none}"
    padding: 6px 10px
    height: 32px
  table-row:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.ink}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.none}"
    padding: 4px 10px
    height: 34px
  table-row-hover:
    backgroundColor: "{colors.surface-1}"
    textColor: "{colors.ink}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.none}"
    padding: 4px 10px
    height: 34px
  table-cell-numeric:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.ink}"
    typography: "{typography.numeric}"
    rounded: "{rounded.none}"
    padding: 4px 10px

  # --- バッジ・タグ（status と損益方向）---
  badge-neutral:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.ink-muted}"
    typography: "{typography.label}"
    rounded: "{rounded.sm}"
    padding: 2px 6px
  badge-up:
    backgroundColor: "{colors.up-weak}"
    textColor: "{colors.up}"
    typography: "{typography.label}"
    rounded: "{rounded.sm}"
    padding: 2px 6px
  badge-down:
    backgroundColor: "{colors.down-weak}"
    textColor: "{colors.down}"
    typography: "{typography.label}"
    rounded: "{rounded.sm}"
    padding: 2px 6px
  badge-pending:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.warning}"
    typography: "{typography.label}"
    rounded: "{rounded.sm}"
    padding: 2px 6px
  badge-approved:
    backgroundColor: "{colors.up-weak}"
    textColor: "{colors.success}"
    typography: "{typography.label}"
    rounded: "{rounded.sm}"
    padding: 2px 6px
  badge-rejected:
    backgroundColor: "{colors.down-weak}"
    textColor: "{colors.danger}"
    typography: "{typography.label}"
    rounded: "{rounded.sm}"
    padding: 2px 6px

  # --- ボタン（pill 廃止。角丸 md・パディング控えめ）---
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "#ffffff"
    typography: "{typography.button}"
    rounded: "{rounded.md}"
    padding: 7px 12px
  button-secondary:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.ink}"
    typography: "{typography.button}"
    rounded: "{rounded.md}"
    padding: 7px 12px
  button-ghost:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.ink-muted}"
    typography: "{typography.button}"
    rounded: "{rounded.md}"
    padding: 7px 12px
  button-icon:
    backgroundColor: "{colors.surface-1}"
    textColor: "{colors.ink}"
    typography: "{typography.button}"
    rounded: "{rounded.md}"
    size: 30px

  # --- 入力 ---
  input:
    backgroundColor: "{colors.surface-1}"
    textColor: "{colors.ink}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.md}"
    padding: 6px 10px
    border: "1px {colors.hairline}"
  input-focused:
    backgroundColor: "{colors.surface-1}"
    textColor: "{colors.ink}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.md}"
    padding: 6px 10px
    border: "1px {colors.accent}"

  # --- タブ（選択は lift で示す。Framer の手法を継承）---
  tab-default:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.ink-muted}"
    typography: "{typography.button}"
    rounded: "{rounded.md}"
    padding: 6px 10px
  tab-selected:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.ink}"
    typography: "{typography.button}"
    rounded: "{rounded.md}"
    padding: 6px 10px

  # --- AI チャット（軸2 相談チャット）---
  chat-bubble-user:
    backgroundColor: "{colors.accent-weak}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.lg}"
    padding: 8px 12px
  chat-bubble-ai:
    backgroundColor: "{colors.surface-1}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.lg}"
    padding: 8px 12px

  # --- チャート枠・ツールチップ ---
  chart-container:
    backgroundColor: "{colors.surface-1}"
    textColor: "{colors.ink-muted}"
    typography: "{typography.caption}"
    rounded: "{rounded.lg}"
    padding: 12px
  tooltip:
    backgroundColor: "{colors.surface-3}"
    textColor: "{colors.ink}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.md}"
    padding: 6px 8px
---

## Overview

AssetVane のデザインシステム。出発点は Framer マーケサイトの解析（旧 `DESIGN.md`）だが、**採用したのは「色」だけ**。Framer のゆったりした配置・巨大 display・pill・マーケ用コンポーネント（pricing card / gradient spotlight / FAQ / footer）は**ダッシュボードでは余白過多・情報密度不足**になるため破棄し、レイアウト体系をゼロから組み直した。

設計の一語は **データ密度（density-first）**。単一ユーザーが日米株のチャート・保有・signals・損益・AI 提案を一望するためのツールなので、**罫線で区切り・行高を詰め・カードの余白を絞る**。余白で「呼吸」させるより、情報を並べて「俯瞰」させることを優先する。

**Key Characteristics:**
- **near-black ダークキャンバス**（`{colors.canvas}`）＋ surface lift（canvas → surface-1 → surface-2 → surface-3）で階層を作る。プロの金融 UI と同じく地は暗い。
- **損益はグローバル標準**: 上昇/利益/買い = `{colors.up}`（緑）、下落/損失/売り = `{colors.down}`（赤）。
- **単一アクセント青** `{colors.accent}` は signal 専用（リンク・focus・選択・アクティブ）。面塗りには使わない（Framer の規律を継承）。
- **タイポは Inter Variable に一本化**。巨大 display を捨て、最大 28px。数値は必ず tabular figures（`{typography.numeric}`）で桁を揃える。
- **4px ベースの詰めた spacing**。Framer の 5/10/15/20/30/96px は破棄。`{spacing.section}` ですら 32px。
- **角丸は控えめ**（4〜12px）、pill は廃止。
- **gradient 4色はチャート系列色に転用**（`{colors.chart-2..4,7}`）。spotlight card という装飾用途は廃止。

## Colors

### サーフェス（階層は色ではなく lift で作る）
- **Canvas**（`{colors.canvas}`）: アプリ全体の地。本文・テーブル行・チャート背景の素地。
- **Surface 1**（`{colors.surface-1}`）: 1段上。サイドバー・カード・テーブルヘッダ・入力欄。
- **Surface 2**（`{colors.surface-2}`）: 2段上。選択行・hover・アクティブタブ・featured。
- **Surface 3**（`{colors.surface-3}`）: 3段上。popover・tooltip・modal（地から最も浮く面）。
- **Hairline / Hairline Soft**（`{colors.hairline}` / `{colors.hairline-soft}`）: 1px 罫線。**区切りの主役**。密度の高い表組みは影ではなく罫線で仕切る。

### テキスト（階層は ink → ink-muted → ink-subtle の3段。太さで盛らない）
- **Ink**（`{colors.ink}`）: 本文・見出し。純白 `#fff` は暗背景で眩しく目が疲れるため僅かに落とす。
- **Ink Muted**（`{colors.ink-muted}`）: ラベル・メタ・副次情報・テーブルヘッダ。
- **Ink Subtle**（`{colors.ink-subtle}`）: プレースホルダ・無効状態。

### アクセント（単一・signal 専用）
- **Accent**（`{colors.accent}`）: リンク・フォーカスリング・選択指標・アクティブ。**ボタンの面塗りは primary のみ許可**、それ以外で青を地に使わない。
- **Accent Weak**（`{colors.accent-weak}`）: 選択タブ地・focus halo・ユーザー発話の吹き出し地。

### 損益・方向（緑=上昇 / 赤=下落）
- **Up**（`{colors.up}`）: 上昇・利益・買いサイド。chart の陽線、P/L のプラス、`side=buy`。
- **Down**（`{colors.down}`）: 下落・損失・売りサイド。chart の陰線、P/L のマイナス、`side=sell`。
- **Flat**（`{colors.flat}`）: 変化なし（`{colors.ink-muted}` と同値）。
- `*-weak` はバッジ地・行ハイライト用の薄背景。

> **損益色は UI セマンティックと値を共有するが用途を分ける**。`success`/`danger` は「操作の成否」、`up`/`down` は「相場の方向」。同じ緑・赤でも意味の文脈が違うので別トークンで持つ。

### セマンティック（UI 状態）
- `success` / `warning` / `danger` / `info`。トースト・バリデーション・`proposals` の status バッジに使う。

### チャート系列色
`{colors.chart-1}`〜`{colors.chart-8}`。Framer の gradient（violet/magenta/orange/coral）を**装飾カードではなく定量チャートの系列色**として転用。複数銘柄・複数業種の重ね描き、相関ヒートマップの離散カテゴリに使う。**損益の up/down 緑赤とは衝突させない**（系列色として緑を使うのは chart-5 の1枠に留め、P/L 表示と混同させない）。

## Typography

### Font Family
- **Inter Variable** 一本。Framer の GT Walsheim（商用フォント）は採用せず捨てた。Inter は OSS で**セルフホスト可能**——ラズパイ・家庭内 LAN 運用で外部 CDN に依存しないため、`woff2` を同梱する。
- OpenType: 本文は `cv11`（alternate 0）等の character variant を有効化。**数値は `tnum`（tabular figures）必須**——価格・損益・株数・比率の桁が縦に揃う。
- 代替候補: **Geist** も near-Inter で可。display 寄りに振りたければ **Mona Sans**。ただし基本は Inter で統一。

### Hierarchy

| Token | Size | Weight | 用途 |
|---|---|---|---|
| `{typography.display}` | 28px | 600 | ページ最上位の見出し（総資産など要所のみ）|
| `{typography.title}` | 20px | 600 | セクションタイトル |
| `{typography.heading}` | 16px | 600 | カード見出し |
| `{typography.subhead}` | 14px | 600 | 小見出し・強調ラベル |
| `{typography.body}` | 14px | 400 | 既定の本文 |
| `{typography.body-sm}` | 13px | 400 | テーブル行・密な本文 |
| `{typography.label}` | 12px | 500 | バッジ・フォームラベル |
| `{typography.caption}` | 11px | 500 caps | テーブルヘッダ・eyebrow・メタ |
| `{typography.numeric}` | 14px | 500 tnum | 表中の数値（価格・損益・株数）|
| `{typography.numeric-lg}` | 22px | 600 tnum | KPI の大きな数値（総資産・P/L）|
| `{typography.button}` | 13px | 500 | ボタン |

### Principles
- **巨大 display を廃止**。ダッシュボードに 110px ヘッドラインの居場所は無い。最大 28px。
- **階層はサイズ＋色（ink/ink-muted）で付ける**。太さの振り幅は 400〜600 に抑える。
- **数値は必ず tabular figures**。桁揃えは金融 UI の最低要件。
- **行間は本文 1.45・テーブル 1.4 と詰める**。読み物ではなく一覧。

## Layout

### 密度の原則（density-first）
- **罫線（`{colors.hairline}`）で区切る。影で浮かせない**。表・リスト・パネルの境界は 1px 罫線。
- **テーブル行高は 32〜34px**。`{components.table-header}` 32px / `{components.table-row}` 34px。
- **カード padding は 12px** が既定（`{components.card}`）。Framer の 24〜32px は使わない。
- **セクション間隔は `{spacing.section}` 32px**。画面の縦を余白で消費しない。
- **コンテンツ幅は制限しない**（マーケの 1199px max は撤廃）。ダッシュボードは横幅をデータで使い切る。

### Grid & Container
- ベースは **CSS Grid / Flex の密なレイアウト**。max-width で中央寄せにせず、サイドバー（`{components.sidebar}` 220px）＋可変メイン領域。
- KPI 行は等幅 4〜6 カラム、その下にテーブル/チャートを敷く構成を標準とする。

### Spacing System
- **4px ベース**: `{spacing.xs}` 4 · `{spacing.sm}` 6 · `{spacing.md}` 8 · `{spacing.lg}` 12 · `{spacing.xl}` 16 · `{spacing.xxl}` 24 · `{spacing.section}` 32。
- セル内パディングは 4〜10px、ボタンは 7px×12px と詰める。

## Elevation & Depth

暗背景での階層は**影ではなく surface lift と罫線**で作る。影は地から大きく浮く要素のみ。

| Level | 手法 | 用途 |
|---|---|---|
| 0 (flat) | 罫線のみ・影なし | テーブル行・本文・パネル既定 |
| 1 (lift) | `{colors.surface-1}` 段上げ | カード・サイドバー・入力 |
| 2 (lift) | `{colors.surface-2}` 段上げ | 選択行・hover・アクティブタブ |
| 3 (float) | `{colors.surface-3}` ＋ `rgba(0,0,0,0.4) 0 8px 24px` ドロップ | popover・tooltip・modal |
| focus | `{colors.accent}` 1px ring（`rgba(0,153,255,0.4) 0 0 0 1px`）| 入力・選択 |

## Shapes

### Border Radius
| Token | Value | 用途 |
|---|---|---|
| `{rounded.sm}` | 4px | バッジ・タグ |
| `{rounded.md}` | 6px | ボタン・入力・タブ・tooltip |
| `{rounded.lg}` | 8px | カード・チャート枠・吹き出し |
| `{rounded.xl}` | 12px | 大きめパネル・modal |
| `{rounded.full}` | 9999px | アバター・丸ドット凡例のみ |

> **pill（100px）は廃止**。Framer では全 CTA が pill だったが、ダッシュボードは角張った矩形の方が密度と整列に向く。

## Components

### App Shell
- **`sidebar`**（220px・`{colors.surface-1}`）に `nav-item` / `nav-item-active` を並べる。アクティブは `{colors.surface-2}` への lift で示す（青の面塗りはしない）。
- **`topbar`**（48px・`{colors.canvas}`）に銘柄検索・データ鮮度バッジ（Free=12週遅延の注記）・現在日付を置く。

### Cards
- **`card`**: padding 12px の情報箱。見出しは `card-header`。**余白を見せ場にしない**。
- **`stat-card`**: KPI 用。`{typography.numeric-lg}` で総資産・P/L・現金比率などを大きく。前日比は `up`/`down` 色＋三角矢印。

### Data Table（密度の主戦場）
- `table-header`（32px・caps の `{typography.caption}`・`{colors.ink-muted}`）。
- `table-row`（34px）＋ `table-row-hover`（`{colors.surface-1}`）。zebra は `{colors.hairline-soft}` で薄く。
- 数値列は `table-cell-numeric`（右寄せ・`tnum`）。**P/L・騰落率のセルは `up`/`down` 色**で文字を着色。

### Badges
- 損益方向: `badge-up` / `badge-down`（薄地＋方向色）。
- `proposals` の status: `badge-pending`（warning）/ `badge-approved`（success）/ `badge-rejected`（danger）。`watchlist` の「最終調査日が古い」等の注意も `badge-pending` 系で。

### Buttons
- `button-primary`（青の面塗りはここだけ許可）/ `button-secondary`（charcoal）/ `button-ghost`（地と同色・hover で lift）/ `button-icon`（30px 角丸）。

### Inputs & Tabs
- `input` / `input-focused`（focus は accent 1px ring）。
- `tab-default` / `tab-selected`（選択は lift で示す）。

### AI Chat（軸2）
- `chat-bubble-user`（`{colors.accent-weak}` 地）/ `chat-bubble-ai`（`{colors.surface-1}` 地）。AI 応答内の数値・根拠は `{typography.numeric}` と `up`/`down` 色を使い、Tool 由来の事実であることを視覚的にも担保する。

### Charts & Tooltip
- `chart-container`（`{colors.surface-1}` 枠）。系列は `chart-1..8`。ロウソク足は陽線 `{colors.up}`・陰線 `{colors.down}`。
- `tooltip`（`{colors.surface-3}`・float）。

## Do's and Don'ts

### Do
- 階層は **surface lift（canvas→1→2→3）と 1px 罫線**で作る。
- 損益・方向は必ず `{colors.up}`（緑）/`{colors.down}`（赤）。チャートのロウソク足・P/L・買い売りで一貫させる。
- 数値は `tnum`（`{typography.numeric}`）で桁を揃える。
- テーブルは行高 32〜34px に詰め、カードは padding 12px に絞る。**密度を最優先**。
- 青（`{colors.accent}`）は signal 専用。リンク・focus・選択・primary ボタンのみ。
- フォントは Inter Variable をセルフホスト（外部 CDN 非依存）。

### Don't
- **Framer のゆったり配置を持ち込まない**。96px section / 24〜32px カード padding / 巨大 display は禁止。
- **pill ボタンを使わない**（角丸 md で矩形に）。
- 青を背景・装飾の面塗りに使わない（primary ボタン以外）。
- gradient を**セクション背景や装飾カード**に使わない。チャート系列色としてのみ転用する。
- 損益の緑赤と、チャート系列の緑を**同じ画面で混同させない**（系列の緑は 1 枠に留める）。
- ink/ink-muted/ink-subtle 以外の中間グレーを増やさない。階層は3段に固定。
- ライトモードを前提に作らない。AssetVane の地は暗い（必要になれば別途追加設計）。

## Responsive Behavior

単一ユーザーが主に**PC の広い画面**で使う前提。ただし topbar の鮮度確認等はスマホからも見る（`docs/architecture.md` の別端末アクセス）。

| 幅 | 変化 |
|---|---|
| Desktop (≥1200px) | サイドバー常時表示・KPI 4〜6 カラム・テーブル全列 |
| Tablet (≥768px) | サイドバー折りたたみ・KPI 2〜3 カラム・テーブルは主要列のみ＋横スクロール |
| Mobile (<768px) | サイドバーはドロワー・KPI 1〜2 カラム・テーブルはカード化 or 主要指標のみ |

- 数値の `tnum` 桁揃えは全幅で維持。
- タッチ環境ではボタン/タブの最小タップ高 36px を確保（padding を縦に少し増やす）。

## Iteration Guide

1. コンポーネントは `components:` のトークン名で参照する（例 `{components.table-row}`・`{components.stat-card}`）。
2. 新セクションを足すとき最初に決めるのは **どの surface lift に載せるか**（canvas / surface-1 / surface-2）。階層判断が最重要。
3. 数値を出す箇所は反射的に `{typography.numeric}` ＋ 損益色を当てる。
4. 編集後に `npx @google/design.md lint DESIGN.md` を流す（`broken-ref` / `contrast-ratio` / `orphaned-tokens` を自動検出）。
5. 新バリアントは別エントリで足す（`-hover` / `-selected` / `-active`）。prose に埋めない。
6. 青は single-shot の signal。2 つ目の青に手が伸びたらブランドが崩れている合図。
7. 「余白で見せたい」と思ったら立ち止まる。このシステムは**密度で見せる**。

## Known Gaps

- 実装スタックは `docs`（Next.js App Router）に従い、本トークンを **Tailwind theme もしくは CSS 変数**へ落とす（shadcn/ui を使うなら CSS 変数に流し込む）。マッピングは Phase 0 のフロント scaffold 時に確定する。
- チャートライブラリ（候補: lightweight-charts / Recharts 等）は未選定。`chart-*` 色とロウソク足の up/down 規約だけ先に固定した。
- ライトモードは未設計（暗一択）。必要になれば surface/ink を反転したペアを別途定義する。
- gradient のストップ値は Framer 解析由来のアンカー hex。チャート系列としては単色で使うため、グラデーション定義は持たない。
