---
name: project-skill-authoring
description: AssetVane のプロジェクト固有スキルを新規作成・編集・リネーム・削除するときに必ず使う。実体は `.skills/<name>/SKILL.md` に置き、`.claude/skills` と `.codex/skills` がリポジトリルートの `.skills/` を指すディレクトリ symlink になっている運用、追加・改名・削除の手順、gitignore allowlist の前提、SKILL.md 本文の書き方（規約は言語化し実装ファイルを手本参照しない）をまとめる。
---

# プロジェクト固有スキル作成フロー

AssetVane のプロジェクト固有スキルは **「実体は `.skills/` 一箇所・`.claude/skills` と `.codex/skills` はそこを指すディレクトリ symlink」** で管理する。Claude Code と Codex CLI のどちらからも同じ `SKILL.md` を参照できるようにするためで、ソースは 1 箇所に保ち、各ツール側はディレクトリ symlink 一本で `.skills/` 全体を共有する（リポジトリ直下の `AGENTS.md -> CLAUDE.md` と同じく、2 ツール併用前提）。

スキルごとに symlink を張る必要はない。`.skills/` 配下にスキルディレクトリを足す・消す・改名するだけで、両ツールに即座に反映される。

## ディレクトリ構造（前提）

```
<repo root>/
├── .skills/                      ← 実体（コミット対象）
│   └── <skill-name>/
│       └── SKILL.md
├── .claude/
│   ├── .gitignore                ← default deny + !skills allowlist
│   └── skills -> ../.skills      ← ディレクトリ symlink（1 本）
└── .codex/
    ├── .gitignore                ← default deny + !skills allowlist
    └── skills -> ../.skills      ← ディレクトリ symlink（1 本）
```

- 実体は **必ず `.skills/<skill-name>/SKILL.md`**。`.claude/skills/` や `.codex/skills/` のパス越しに実ファイルを作らない（symlink 越しなので結局 `.skills/` に書き込まれるが、混乱を避けるため操作は必ず `.skills/` 側のパスで行う）。
- `.claude/skills` と `.codex/skills` は **相対パス `../.skills`** を指すディレクトリ symlink。絶対パスは別環境で壊れるので使わない。
- このディレクトリ symlink 自体は一度張れば済む。**通常のスキル作業（追加・改名・削除）で symlink を触ることはない。**

## 新規スキル追加の手順

スキル名 `<skill-name>` は kebab-case。

### 1. 実体ディレクトリを作成し SKILL.md を書く

```bash
mkdir -p .skills/<skill-name>
```

フロントマターは既存スキルと同じ形式に揃える:

```markdown
---
name: <skill-name>
description: <いつ必ず使うか / 何を判定・規定するか。発火条件を含めて 1〜2 文で。>
---

# <スキルタイトル>

<本文>
```

`description` は「**いつ呼び出すべきか**」が一読でわかるように書く。スキル選択器はこの 1 行で発火判定するため、「〜のときに必ず使う」「〜を判定・規定する」など発火条件と効能を具体的に書く。本文・description はすべて日本語（プロジェクト全体の言語方針）。

### 2. これで完了

`.claude/skills` / `.codex/skills` はディレクトリ symlink なので、`.skills/<skill-name>/` を作った時点で両ツールから参照可能になる。symlink を張る作業も個別の解決確認も不要。

念のため確認したい場合のみ:

```bash
test -f .claude/skills/<skill-name>/SKILL.md && echo "claude OK"
test -f .codex/skills/<skill-name>/SKILL.md && echo "codex OK"
```

### gitignore は触らない

`.claude/.gitignore` と `.codex/.gitignore` はすでに以下の方針で書かれているため、新規スキル追加時に gitignore を触る必要はない:

```
*
!.gitignore
!skills
```

`skills` はディレクトリではなく symlink なので、git/jj は symlink ファイル 1 個として扱う。`!skills`（末尾スラッシュなし）でその symlink が allow され、リンク先 `.skills/` の実体はリポジトリルートの `.gitignore` で除外されていない（`.skills` は無視対象に入っていない）ため普通にコミットされる。

> 注意: 末尾スラッシュ付きの `!skills/` はディレクトリにしかマッチしないため symlink には効かない。`!skills/**` も symlink を辿らないので無意味。allowlist は必ず `!skills` と書く。

新しいトップレベルディレクトリを `.claude/` / `.codex/` 配下に作りたい場合のみ、それぞれの `.gitignore` に `!<dir>` の allow を明示する。

## リネーム

実体ディレクトリ名を変えるだけ。symlink は `.skills/` 全体を指しているので張り直し不要。

```bash
mv .skills/<old-name> .skills/<new-name>
```

`SKILL.md` 内のフロントマター `name:` も `<new-name>` に合わせて更新する。`description` のトリガー文言も整合させる。

## 削除

実体ディレクトリを消すだけ。

```bash
rm -r .skills/<skill-name>
```

その後、他スキルや `CLAUDE.md` / `AGENTS.md` / `docs/` などからの参照が残っていないかを `grep -rn "<skill-name>" .` で確認する。

## SKILL.md 本文の書き方 ― 規約は言語化する

スキルは**規約そのもの（標準）を言語化し、必要なら SKILL.md 内に自己完結したコード例を書く**。コードベースの特定の実装ファイル・クラス・関数を「リファレンス実装」「実例」として名指しし、「これに揃えろ」と参照させてはいけない。

理由: 実装は変わる。スキルが特定の実装ファイルを指すと、そのファイルの改名・削除・パターン逸脱でスキルが腐り、エージェントを誤誘導する。規約と実装のどちらが正かも曖昧になる。

- ✅ 規約を文章で定義する／SKILL.md 内に自己完結したコード例を置く／命名は `<Model>Out` のような雛形で示す
- ✅ 配置ルールとしてのディレクトリ（`app/routers/` に置く・`components/ui/` に置く 等）、生成物・設定ファイルの場所、他スキル名の参照
- ✅ **設計判断の出所として ADR 番号・`docs/` 参照を引く**（例: `（ADR-014）`・`（docs/api.md §1）`）。これは「手本実装の参照」ではなく「意図の典拠」なので推奨
- ❌ 「`PortfolioRouter` がリファレンス実装」「`signals.py` が実例」のように実装ファイル・クラスを手本として参照させる

現存コードが規約と食い違う場合、既定は**規約が正**（ドリフトは別途リファクタで詰める。スキルにその旨を一文添えてよい）。ただし規約自体を見直すべきと判断したら、コードへ寄せる前に**変更案を理由付きで提案し、ユーザーの承認を得てから**スキルを改める。規約は正本だが不変ではなく、提案＋承認でブラッシュアップする。

### ADR と一般的ベストプラクティスが衝突するとき

AssetVane には ADR で意図的に「普通こうする」を上書きした不変条件がある（例: ADR-005 で Next は DB に触らずブラウザ fetch のみ＝Server Component でのサーバー側取得を採らない）。スキルにベストプラクティスを書くときは:

- **ADR が常に勝つ**。一般論を理由に ADR を破る書き方をスキルに載せない。
- 衝突する一般的ベストは、**「一般には××だが、本プロジェクトは ADR-××で意図的に逆を選ぶ」と逸脱を明記**する。黙って一般論を書くと、後でエージェントが ADR を破る方向に誘導される。
- ただし **ADR 自体が明らかに陳腐化している**と判明した場合は、黙って従わずユーザー（および `docs/decisions.md`）に上げる。

## バージョン管理メモ

このリポジトリは **Jujutsu（jj）** で管理する（git ではない）。jj も `.gitignore` を読むため allowlist の前提は同じ。**コミットは指示があった時だけ**行う。スキル追加・改名・削除は作業ツリー上の変更として残るので、コミット指示を待つ。

## チェックリスト（作業完了前に必ず確認）

- [ ] 実体は `.skills/<skill-name>/SKILL.md` に存在する
- [ ] フロントマターの `name:` がディレクトリ名と一致している
- [ ] フロントマターの `description:` に発火条件が日本語で書かれている
- [ ] （任意）`.claude/skills/<skill-name>/SKILL.md` / `.codex/skills/<skill-name>/SKILL.md` でファイルまで辿れる
- [ ] 本文が特定の実装ファイル・クラスを手本参照していない（規約は言語化・自己完結コード例で示した）
- [ ] ADR と衝突する一般論は「意図的逸脱」として明記したか、そもそも書いていない

## やってはいけないこと

- `.claude/skills/...` や `.codex/skills/...` のパスでスキルを新規作成・編集する。symlink 越しなので結果は同じだが、運用上の混乱を避けるため操作は必ず `.skills/` 側のパスで行う
- ディレクトリ symlink を **絶対パス**で張り直す（例: `ln -s /Users/.../.skills .claude/skills`）。別マシン・別チェックアウトで壊れる。相対パス `../.skills` を使う
- gitignore の allowlist を `!skills/`（末尾スラッシュ付き）にする。symlink はディレクトリ扱いされないのでマッチしない。必ず `!skills`
- スキルごとに個別 symlink を張る。ディレクトリ symlink がある現構成では不要かつ管理コスト増
- コードベースの実装ファイル・クラスを「リファレンス実装」として SKILL.md に参照させる（規約は言語化・コード例で示す）
