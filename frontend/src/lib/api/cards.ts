import { delNoContent, getJSON, postJSON, putJSON } from "./_client";

// 知識カード（ADR-062 追補）。本文だけで追加でき、AI が title/when_to_apply/level を整える。
// weight=重要度（>0・既定 1.0）で「古い/信頼度低いカード」を下げられる。

// 知識カード（ADR-062・docs/api.md「知識カード」・routers/cards.py と 1:1）。
// 「規律は CORE、一般常識は LLM、ここは“非自明な知識”を置く」層。AI 審査（triage）で status を
// 振り分け、人間が active 化する（本番助言に効く操作は人間承認＝ADR-009）。
// 型は backend Pydantic（CardOut/CardCreateIn/CardUpdateIn/TriageOut/TriageResponse）と
// フィールド名・null 許容まで厳密に 1:1。embedding BLOB は返らない（embedded_at だけ来る）。

/** カードの状態（backend CardStatus と 1:1）。draft=未審査 / active=本番助言に効く /
 * needs_quant=新計算が要る / to_core=CORE 昇格候補 / rejected=却下。 */
export type CardStatus = "draft" | "active" | "needs_quant" | "to_core" | "rejected";

/** カードの階層（backend CardLevel と 1:1）。stock/sector/market/general。 */
export type CardLevel = "stock" | "sector" | "market" | "general";

/** 知識カードの公開表現（ADR-062・embedding BLOB は含まない）。
 * status は backend が確定（triage/activate で変わる）・フロントで再計算しない。 */
export type CardOut = {
  id: number;
  title: string;
  body: string;
  when_to_apply: string | null;
  status: string; // CardStatus 文字列（draft/active/needs_quant/to_core/rejected）
  level: string | null; // CardLevel 文字列
  sector17_code: string | null;
  theme: string | null;
  linked_signal_type: string | null; // triage が紐づけた実装済みシグナル種別
  quant_note: string | null; // needs_quant の補足（必要な新計算のメモ）
  always_inject: boolean; // 常時注入カードか
  source: string | null; // 出所（任意 URL 等）
  weight: number; // 重要度（>0・既定 1.0）。古い/信頼度低いカードを下げる用途（ADR-062 追補）
  triage_reason: string | null; // 追加時 AI 審査の判定理由（null=AI 未整形・ADR-062 追補）
  embedded_at: string | null; // 埋め込み済みかの UI ヒント（null=未埋め込み）
  created_at: string | null;
  updated_at: string | null;
};

/** カード作成リクエスト（backend CardCreateIn と 1:1・ADR-062 追補「雑追加」リデザイン）。
 * 本文（＋出所 URL）だけ。title/when_to_apply/level は追加時に AI が生成する。status は backend が
 * verdict から決める（active 候補は draft 留置＝人間承認待ち）。 */
export type CardCreateIn = {
  body: string;
  source?: string | null;
};

/** カード部分更新リクエスト（backend CardUpdateIn と 1:1・全て optional）。
 * 渡したフィールドだけ更新される（exclude_unset）。 */
export type CardUpdateIn = {
  title?: string | null;
  body?: string | null;
  when_to_apply?: string | null;
  level?: CardLevel | null;
  sector17_code?: string | null;
  theme?: string | null;
  source?: string | null;
  linked_signal_type?: string | null;
  quant_note?: string | null;
  always_inject?: boolean;
  weight?: number; // 重要度（>0・ADR-062 追補）
};

/** AI 審査の結果（backend TriageOut と 1:1）。 */
export type TriageOut = {
  verdict: string; // active / needs_quant / to_core / rejected
  reason: string;
  quant_note: string | null;
  linked_signal_type: string | null;
};

/** 審査エンドポイントの応答（backend TriageResponse と 1:1）。
 * triage=null は面未設定/応答不正で審査できなかったとき（status 据え置き・ADR-018）。 */
export type TriageResponse = {
  triage: TriageOut | null;
  card: CardOut;
};

/** 知識カード一覧（status で絞り込み可・省略で全件・新しい順）。 */
export function getCards(status?: CardStatus, signal?: AbortSignal): Promise<CardOut[]> {
  const qs = status ? `?status=${encodeURIComponent(status)}` : "";
  return getJSON<CardOut[]>(`/cards${qs}`, signal);
}

/** 知識カードを 1 件取得（無ければ 404 で ApiError throw）。 */
export function getCard(id: number, signal?: AbortSignal): Promise<CardOut> {
  return getJSON<CardOut>(`/cards/${id}`, signal);
}

/** カードを作成（201・status は backend が "draft" 固定）。 */
export function postCard(body: CardCreateIn): Promise<CardOut> {
  return postJSON<CardOut>("/cards", body);
}

/** カードを部分更新（渡したフィールドだけ更新）。 */
export function putCard(id: number, body: CardUpdateIn): Promise<CardOut> {
  return putJSON<CardOut>(`/cards/${id}`, body);
}

/** カードを削除（204・本文なし）。無ければ 404 で ApiError throw。 */
export function deleteCard(id: number): Promise<void> {
  return delNoContent(`/cards/${id}`);
}

/** 既存カードを AI で再整形する（AI 未整形の再試行＋編集後の再審査・アクション動詞・ADR-062 追補）。
 * 保存済み本文から title/when_to_apply/level を生成し直し、verdict→status・triage_reason を更新する。
 * verdict='active' は人間承認を待つため status は draft 据え置き。 */
export function assistCard(id: number): Promise<TriageResponse> {
  return postJSON<TriageResponse>(`/cards/${id}/assist`, {});
}

/** カードを active 化（人間の最終承認＝本番助言に効く・ADR-009/062）。 */
export function activateCard(id: number): Promise<CardOut> {
  return postJSON<CardOut>(`/cards/${id}/activate`, {});
}
