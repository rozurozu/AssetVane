# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## このプロジェクトについて

**AssetVane** は、日米の株式を分析し、**AI と投資方針を相談しながら銘柄・配分を提案する**、個人投資家 1 人用の投資ダッシュボード。自動売買はせず、提示に徹する。

**現状は「設計フェーズ」で、アプリのコードはまだ無い**（`frontend/` も `backend/` も未作成）。設計の真実は `docs/` にある。実装を始める前に必ず `docs/` を読むこと。

## ドキュメントの地図（実装前に読む）

| 読むもの | 内容 |
|---|---|
| `README.md` | 全体像・技術スタック・起動手順 |
| `docs/decisions.md` | **ADR-001〜025。なぜそうしたかの全記録。最重要** |
| `docs/architecture.md` | システム構成・2 軸 AI・データフロー・通信/障害/運用 |
| `docs/screens.md` | 画面設計（IA）・ナビ方針・Dashboard 構成・常駐 Advisor チャット・画面コンテキスト |
| `docs/advisor.md` | AI Advisor の設計（CORE/POLICY プロンプト・Tool・手法の扱い） |
| `docs/data-model.md` | DB スキーマ（全テーブル） |
| `docs/api.md` | REST API 契約（Next ↔ FastAPI の境界） |
| `docs/jquants.md` | J-Quants API V2 の認証・プラン・エンドポイント |
| `docs/roadmap.md` | Phase 0〜7。**Phase 0 の縦スライスから着手する** |

## アーキテクチャの不変条件（default の直感を上書きする。違反しないこと）

これらは ADR で決まった意図的な選択。「普通こうする」を理由に破らないこと。

- **DB に触れるのは FastAPI だけ。Next.js は UI 専用で、データは REST 経由で取る**（ADR-005）。**Prisma は使わない**。Next 側に DB アクセスを足さない。
- **AI に数値を計算させない**。Python が「事実（数字）」を計算し、LLM は Tool Calling で受け取った事実を**解釈・提案するだけ**（ADR-014）。プロンプトに生データを丸投げしない。
- **Advisor チャット（軸2）は全ページ常駐**（フローティング・Next の root layout・ページ遷移で会話保持）（ADR-024）。専用ページに閉じ込めない。
- **画面コンテキストは軽量ヒントのみ**（見ているページ＋主対象）。チャットに画面の数値を載せず、必要時に Tool で取り直す（ADR-025）。ADR-014 と同じ規律。
- **手法（一目均衡表・モメンタム・リードラグ等）は必ずテスト済みコードで実装する**。LLM にその場でコードを書かせて計算させない（再現性・backtest が壊れる＝ADR-016）。「手法 DB」は索引であってコードの代替ではない。
- **システムプロンプトは「不変 CORE（リポジトリの prompt ファイル）＋ 可変 POLICY（DB の `policy`）」に分離**（ADR-015）。CORE はチャットで書き換えない。専門性は CORE・Tool・手法カードに宿す。
- **投資方針 `policy` は単一を育てる**。複数ペルソナ切り替えや版管理機構は作らない。履歴は `advisor_journal` のスナップショットで残す（ADR-013）。
- **J-Quants は V2（`x-api-key` ヘッダー）を使う**。V1 のトークン 2 段階方式（`/v1/...`）は 2026-06-01 に終了済み。ネット上の記事は大半が古い V1 なので流用しない（ADR-008）。J-Quants は**日本株専用**。
- **通知は Discord Webhook**。LINE Notify は終了済みなので使わない（ADR-007）。
- **SQLite（WAL）。書き手は夜間バッチ（Python）1 つに限定**してロック競合を避ける（ADR-002）。再取得で壊れないよう UPSERT で冪等にする。
- **単一ユーザー・認証なし**（ADR-001）。`user_id` を足さない。家庭内 LAN 前提で外部公開しない。
- **データソースはアダプタ越し**（`JQuantsAdapter` / `IndexAdapter` / `UsEquityAdapter` / `NewsAdapter`）。直結ハードコードしない（ADR-010）。
- **銘柄ドシエは DB に保存**（`stock_dossiers` の markdown 列）＋ソース台帳（`dossier_sources`、本文は持たず要約＋URL）。リポジトリ markdown には置かない（AI が頻繁に自動更新するため＝ADR-020）。逆に CORE プロンプト・手法カード（参照知識）は安定資産なのでリポジトリ markdown に置く。
- **重い処理の置き場所**: ML 学習は別 PC（ラズパイは `.pkl` で推論のみ＝ADR-006）。LLM 推論は OpenRouter（クラウド、`.env` で差替可＝ADR-012）。MCP によるニュース取得は昼チャットでは使えるが、**無人 cron では使えないことがある**ので夜は軽め（ADR-020）。

## 開発コマンド（Phase 0 で scaffold 後に有効）

2 プロセス構成（backend ＋ frontend）。両方起動する。

```bash
# Backend (FastAPI) — 別端末から見るため 0.0.0.0 で待ち受け
cd backend && source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Frontend (Next.js)
cd frontend && npm run dev
```

- 接続先は frontend の `NEXT_PUBLIC_API_BASE_URL`、CORS は backend の `.env` の `CORS_ALLOW_ORIGINS`。
- 秘密情報（J-Quants / LLM のキー）は **backend の `.env` のみ**。frontend には渡さない。
- 開発は J-Quants **Free プラン**（株価 12 週間遅延）で進む。評価額・P/L も遅延値になる点に注意。

## バージョン管理・言語

- **バージョン管理は Jujutsu（`jj`）を使う**（git ではなく）。コミットは指示があった時だけ行う。
- **ドキュメント・コメント・会話は日本語**で書く。
