import { getJSON, putJSON } from "./_client";

// 投資家プロファイル（ADR-082・routers/profile.py と 1:1）。policy（規範）と分離した「記述＝行動の
// 癖」の単一行ドキュメント。夜バッチ profiler が台帳から傾向メモ（profile_note）を承認制で起票し、
// 人間が承認すると本文へ追記される。承認/却下は既存 approveProposal/rejectProposal（advisor.ts）。

/** 投資家プロファイル（backend InvestorProfile と 1:1）。body は散文 1 枚・未育成は空文字。 */
export type InvestorProfile = {
  body: string;
  updated_at: string | null;
};

/** pending の傾向メモ（backend ProfileNote と 1:1・proposals kind='profile_note' を parse）。 */
export type ProfileNote = {
  id: number;
  text: string;
  evidence: string;
  created_date: string;
};

/** 現在の投資家プロファイル（active 文書）を取得する。 */
export function getProfile(signal?: AbortSignal): Promise<InvestorProfile> {
  return getJSON<InvestorProfile>("/profile", signal);
}

/** 投資家プロファイル本文を手編集で全文置換する（人間による編集＝ADR-009）。 */
export function putProfile(body: string): Promise<InvestorProfile> {
  return putJSON<InvestorProfile>("/profile", { body });
}

/** 承認待ちの傾向メモ一覧を取得する（承認/却下は approveProposal/rejectProposal を使う）。 */
export function getProfileNotes(signal?: AbortSignal): Promise<ProfileNote[]> {
  return getJSON<ProfileNote[]>("/profile/notes", signal);
}
