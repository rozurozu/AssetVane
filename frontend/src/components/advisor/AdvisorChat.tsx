"use client";

// 相談チャットAI（軸2）の常駐フローティング枠（ADR-024・screens.md §4・spec §9.1）。
// root layout に置きページ遷移で会話を保持（会話状態は AdvisorChatProvider・ADR-065）。
// 本コンポーネントは「枠」だけを持つ＝ヘッダー（ドラッグ）／窓サイズ・位置の永続／最小化／
// リサイズ。会話本体（メッセージ・入力・journal 昇格）は ChatConversation に委譲する。
// - 専用ページ /advisor では二重表示しないため null を返す（ADR-065・OPEN-I 撤回）。
// - ドラッグ／リサイズ／最小化: 依存を増やさず自前 pointer ハンドル（OPEN-H）。

import { ChatConversation } from "@/components/advisor/ChatConversation";
import { onOpenAdvisorChat } from "@/lib/advisor-bus";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

// 窓サイズ/位置の localStorage キー（会話本体の永続は Provider 側の別キー）。
const LS_RECT = "advisor.rect.v1";

const MIN_W = 320;
const MIN_H = 360;

type Rect = { w: number; h: number };

export function AdvisorChat() {
  const pathname = usePathname();
  const [open, setOpen] = useState(true);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
  const [rect, setRect] = useState<Rect>({ w: 360, h: 520 });
  const [hydrated, setHydrated] = useState(false);
  const drag = useRef<{ x: number; y: number; left: number; top: number } | null>(null);
  const resize = useRef<{ x: number; y: number; w: number; h: number } | null>(null);
  const chatRef = useRef<HTMLDivElement>(null);

  // localStorage から窓サイズを復元（マウント後 1 回）。
  useEffect(() => {
    try {
      const r = localStorage.getItem(LS_RECT);
      if (r) setRect(JSON.parse(r) as Rect);
    } catch {
      // 壊れた JSON は無視して既定サイズで始める。
    }
    setHydrated(true);
  }, []);

  // 窓サイズの永続。
  useEffect(() => {
    if (!hydrated) return;
    try {
      localStorage.setItem(LS_RECT, JSON.stringify(rect));
    } catch {}
  }, [rect, hydrated]);

  // nav 以外（Policy 等）からの open 要求を購読（OPEN-I・advisor-bus）。
  useEffect(() => onOpenAdvisorChat(() => setOpen(true)), []);

  const onPointerDown = useCallback(
    (e: React.PointerEvent) => {
      const el = chatRef.current;
      if (!el) return;
      const r = el.getBoundingClientRect();
      drag.current = {
        x: e.clientX,
        y: e.clientY,
        left: pos?.left ?? r.left,
        top: pos?.top ?? r.top,
      };
      (e.target as HTMLElement).setPointerCapture(e.pointerId);
    },
    [pos],
  );

  const onPointerMove = useCallback((e: React.PointerEvent) => {
    const d = drag.current;
    const el = chatRef.current;
    if (!d || !el) return;
    const left = Math.max(
      4,
      Math.min(d.left + (e.clientX - d.x), window.innerWidth - el.offsetWidth - 4),
    );
    const top = Math.max(4, Math.min(d.top + (e.clientY - d.y), window.innerHeight - 50));
    setPos({ left, top });
  }, []);

  const onPointerUp = useCallback(() => {
    drag.current = null;
  }, []);

  // リサイズハンドル（右下角・自前 pointer・依存なし＝OPEN-H）。
  const onResizeDown = useCallback(
    (e: React.PointerEvent) => {
      e.stopPropagation();
      resize.current = { x: e.clientX, y: e.clientY, w: rect.w, h: rect.h };
      (e.target as HTMLElement).setPointerCapture(e.pointerId);
    },
    [rect],
  );

  const onResizeMove = useCallback((e: React.PointerEvent) => {
    const r = resize.current;
    if (!r) return;
    const w = Math.max(MIN_W, Math.min(r.w + (e.clientX - r.x), window.innerWidth - 8));
    const h = Math.max(MIN_H, Math.min(r.h + (e.clientY - r.y), window.innerHeight - 8));
    setRect({ w, h });
  }, []);

  const onResizeUp = useCallback(() => {
    resize.current = null;
  }, []);

  // 専用ページでは大画面チャットが本体を描くので、フローティングは出さない（ADR-065）。
  if (pathname === "/advisor") return null;

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="fixed right-4 bottom-4 z-[60] flex items-center gap-2 rounded-lg bg-accent px-3.5 py-2.5 font-medium text-[13px] text-white shadow-[0_8px_24px_rgba(0,0,0,0.4)]"
      >
        🧠 Advisor に相談
      </button>
    );
  }

  const style = pos
    ? {
        left: pos.left,
        top: pos.top,
        right: "auto" as const,
        bottom: "auto" as const,
        width: rect.w,
        height: rect.h,
      }
    : { right: 16, bottom: 16, width: rect.w, height: rect.h };

  return (
    <div
      ref={chatRef}
      style={style}
      className="fixed z-[65] flex flex-col overflow-hidden rounded-xl border border-hairline bg-surface-1 shadow-[0_8px_24px_rgba(0,0,0,0.5)]"
    >
      {/* ヘッダー：掴んでドラッグ移動 */}
      <div
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        className="flex cursor-move items-center gap-2 border-hairline border-b bg-surface-2 px-3 py-2.5"
      >
        <span className="grid h-[22px] w-[22px] place-items-center rounded-md bg-accent text-[12px] text-white">
          🧠
        </span>
        <b className="font-semibold text-[13px]">AI Advisor</b>
        <span className="flex items-center gap-1 text-[11px] text-up">
          <span className="h-[5px] w-[5px] rounded-full bg-up" />
          オンライン
        </span>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="ml-auto h-6 w-6 rounded-md text-[14px] text-ink-muted hover:bg-hairline hover:text-ink"
        >
          ✕
        </button>
      </div>

      {/* 会話本体（専用ページと共有・ADR-065）。フローティングは画面コンテキストヒントを出す。 */}
      <ChatConversation showContextHint />

      {/* リサイズハンドル（右下角・自前 pointer・OPEN-H）。 */}
      <div
        onPointerDown={onResizeDown}
        onPointerMove={onResizeMove}
        onPointerUp={onResizeUp}
        className="absolute right-0 bottom-0 h-4 w-4 cursor-nwse-resize"
        style={{
          background:
            "linear-gradient(135deg, transparent 50%, var(--color-ink-subtle) 50%, var(--color-ink-subtle) 60%, transparent 60%)",
        }}
      />
    </div>
  );
}
