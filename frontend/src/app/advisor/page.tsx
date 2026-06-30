"use client";

// AI Advisor 専用チャットページ（大画面・ADR-065・OPEN-I 撤回）。
// メニュー「Advisor」の遷移先。会話状態はフローティングと共有（AdvisorChatProvider）するので、
// /advisor ↔ 他ページを行き来しても同一の会話が続く（ADR-024）。フローティング窓は /advisor では
// 非表示（AdvisorChat 側で null・二重表示を避ける）。本体は ChatConversation を縦いっぱいに描く。
// 画面コンテキストヒントはチャットそのものなので出さない（showContextHint=false）。

import { ChatConversation } from "@/components/advisor/ChatConversation";

export default function AdvisorPage() {
  return (
    // Topbar 48px ＋ main の p-4（上下 16px）を差し引いた高さで枠を縦いっぱいに。
    <div className="flex h-[calc(100vh-6rem)] flex-col overflow-hidden rounded-lg border border-hairline bg-surface-1">
      <div className="flex items-center gap-2 border-hairline border-b bg-surface-2 px-3 py-2.5">
        <span className="grid h-[22px] w-[22px] place-items-center rounded-md bg-accent text-[12px] text-white">
          🧠
        </span>
        <b className="font-semibold text-[14px]">AI Advisor</b>
        <span className="flex items-center gap-1 text-[11px] text-up">
          <span className="h-[5px] w-[5px] rounded-full bg-up" />
          オンライン
        </span>
        <span className="ml-auto text-[11px] text-ink-subtle">
          投資方針・銘柄の相談／知識ノートの追加（数値は Tool で取り直す・ADR-014）
        </span>
      </div>
      <ChatConversation showContextHint={false} />
    </div>
  );
}
