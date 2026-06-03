// 常駐 Advisor チャット（軸2・ADR-024）を別コンポーネントから開くための軽量バス。
// nav「Advisor」は専用ページを作らずチャット起動トリガにする（spec §9.1・OPEN-I）。
// 依存を増やさず、window CustomEvent で AdvisorChat に open を通知するだけ（root layout 同居前提）。

const OPEN_EVENT = "advisor:open";

/** Advisor チャットを開くよう要求する（Sidebar / 各画面の「チャットで調整」導線から呼ぶ）。 */
export function openAdvisorChat(): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(OPEN_EVENT));
}

/** AdvisorChat 側で open 要求を購読する。解除関数を返す。 */
export function onOpenAdvisorChat(handler: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  window.addEventListener(OPEN_EVENT, handler);
  return () => window.removeEventListener(OPEN_EVENT, handler);
}
