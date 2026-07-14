"use client";

// J-Quants のプラン状態を「表示文言」に変換する共有ヘルパ（ADR-061・ADR-071）。
// 事実（plan / delay_days / configured）は backend が /health で配る（services/jquants_config.plan_status・
// 遅延日数は _PLAN_DELAY_DAYS 定数）。フロントは受け取った事実を文言に組むだけで、プラン名や遅延幅を
// 焼き込まない（ADR-014 と同じ規律＝ハードコードすると /settings でプランを変えても嘘が残る）。
//
// 書き分けの芯（ADR-071）: 「プラン由来の構造的な遅延」（Free は物理的に約 12 週遅れ）と、
// 「as_of の鮮度実測から来る古さ（stale）」は別物。is_delayed は後者（有料プランでも夜間バッチが
// 止まれば true になる）なので、遅延ありの理由をプランに決めつけない。

import { type HealthResponse, type JquantsStatus, getHealth } from "@/lib/api";
import { useApi } from "@/lib/use-api";

/** /health から J-Quants のプラン状態を取る（未取得・取得失敗時は undefined）。 */
export function useJquantsStatus(): JquantsStatus | undefined {
  const { data } = useApi<HealthResponse>((signal) => getHealth(signal), []);
  return data?.jquants;
}

/** プラン名を表示用に先頭大文字化（"free" → "Free"）。空なら空文字。 */
export function titleCasePlan(plan: string): string {
  return plan ? plan.charAt(0).toUpperCase() + plan.slice(1) : "";
}

/** 契約プラン由来の遅延（"Free・株価12週遅延" / "Light・遅延なし"）。未設定・未取得は null。 */
export function planDelayLabel(jq: JquantsStatus | undefined): string | null {
  if (!jq || !jq.configured) return null;
  const plan = titleCasePlan(jq.plan);
  if (jq.delay_days <= 0) return `${plan}・遅延なし`;
  const weeks = Math.round(jq.delay_days / 7); // free: 84/7=12
  return `${plan}・株価${weeks}週遅延`;
}

/** データ源の注記（"J-Quants Free・株価12週遅延" / "J-Quants 未設定" / "J-Quants 確認中…"）。 */
export function jquantsSourceNote(jq: JquantsStatus | undefined): string {
  if (!jq) return "J-Quants 確認中…"; // 初回 /health 前
  const label = planDelayLabel(jq);
  return label ? `J-Quants ${label}` : "J-Quants 未設定"; // api_key 未登録は /settings 誘導の含意
}

/** 契約プラン由来の構造的な株価遅延があるか（Free=84 日／有料=0）。未設定・未取得は false。 */
export function hasPlanDelay(jq: JquantsStatus | undefined): boolean {
  return (jq?.configured ?? false) && (jq?.delay_days ?? 0) > 0;
}

/** 遅延ありの理由（基準日を書けない短い meta 用）。プラン由来なら "Free・株価12週遅延"。 */
export function delayReasonLabel(jq: JquantsStatus | undefined): string {
  const label = planDelayLabel(jq);
  return hasPlanDelay(jq) && label ? label : "データが古い";
}

/** 基準日つきの鮮度注記（カード meta・見出し用）。as_of が無ければ undefined。
 *
 * isDelayed は as_of の鮮度実測（ADR-071）。プラン由来の遅延（Free）ならプラン名で理由を示し、
 * 遅延なしプランなのに古い（＝夜間バッチ未実行等の stale）なら理由を決めつけず「データが古い」と言う。
 */
export function freshnessNote(
  jq: JquantsStatus | undefined,
  asOf: string | null | undefined,
  isDelayed: boolean,
): string | undefined {
  if (!asOf) return undefined;
  if (!isDelayed) return `${asOf} 基準`;
  if (hasPlanDelay(jq)) return `${jquantsSourceNote(jq)}・${asOf} 基準`;
  return `${asOf} 基準（データが古い）`;
}
