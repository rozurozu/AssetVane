"use client";

import { useCallback, useEffect, useRef, useState } from "react";

// 相談チャットAI（軸2）の常駐フローティング UI（ADR-024）。
// 実 LLM 配線版（最小）。会話はクライアント保持（ステートレスなサーバへ毎ターン messages を送る）。
// 永続化（localStorage / DB）・ストリーミング・画面コンテキスト送信は後続（下の TODO 参照）。
const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

type Msg = { role: "user" | "assistant"; content: string };

export function AdvisorChat() {
  const [open, setOpen] = useState(true);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const drag = useRef<{ x: number; y: number; left: number; top: number } | null>(null);
  const chatRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  // 新しいメッセージが来たら最下部へ。
  // biome-ignore lint/correctness/useExhaustiveDependencies: messages 変化時に末尾へスクロール
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages.length, busy]);

  const send = useCallback(async () => {
    const text = input.trim();
    if (!text || busy) return;
    const next: Msg[] = [...messages, { role: "user", content: text }];
    setMessages(next);
    setInput("");
    setBusy(true);
    try {
      const r = await fetch(`${API}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: next }),
      });
      if (!r.ok) {
        const detail = await r
          .json()
          .then((j) => j.detail as string)
          .catch(() => `HTTP ${r.status}`);
        throw new Error(detail);
      }
      const data = (await r.json()) as { reply: string };
      setMessages((m) => [...m, { role: "assistant", content: data.reply }]);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setMessages((m) => [
        ...m,
        { role: "assistant", content: `⚠ Advisor に繋がらなかった: ${msg}` },
      ]);
    } finally {
      setBusy(false);
    }
  }, [input, busy, messages]);

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
    ? { left: pos.left, top: pos.top, right: "auto" as const, bottom: "auto" as const }
    : { right: 16, bottom: 16 };

  return (
    <div
      ref={chatRef}
      style={style}
      className="fixed z-[65] flex h-[520px] w-[360px] flex-col overflow-hidden rounded-xl border border-hairline bg-surface-1 shadow-[0_8px_24px_rgba(0,0,0,0.5)]"
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

      {/* 画面コンテキスト（指示語解決のヒント・数値は渡さない＝ADR-025）。
          TODO(adr-025): いまは静的表示のみ。将来 page/focus を /chat の body に載せて送る。 */}
      <div className="border-hairline-soft border-b bg-canvas px-3 py-1.5 text-[11px] text-ink-muted">
        📍 見ているページ: <b className="text-accent">Dashboard</b> ・ 画面の数字を文脈に相談できる
      </div>

      <div ref={scrollRef} className="flex flex-1 flex-col gap-2.5 overflow-auto p-3">
        {messages.length === 0 && (
          <div className="m-auto px-4 text-center text-[12px] text-ink-subtle leading-[1.6]">
            投資方針や銘柄の考え方を相談できるのだ。
            <br />
            （いまは事実取得 Tool 未接続＝一般論ベース）
          </div>
        )}
        {messages.map((m, i) => (
          // biome-ignore lint/suspicious/noArrayIndexKey: 追記のみで並びは不変
          <Bubble key={i} who={m.role === "user" ? "u" : "a"}>
            {m.content}
          </Bubble>
        ))}
        {busy && (
          <Bubble who="a">
            <span className="text-ink-subtle">考え中…</span>
          </Bubble>
        )}
      </div>

      <div className="flex gap-2 border-hairline border-t px-3 py-2.5">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.nativeEvent.isComposing) {
              e.preventDefault();
              send();
            }
          }}
          disabled={busy}
          placeholder="この画面について質問…（例: 短期で攻める方針を相談したい）"
          className="flex-1 rounded-md border border-hairline bg-canvas px-2.5 py-1.5 text-[13px] text-ink outline-none focus:border-accent disabled:opacity-60"
        />
        <button
          type="button"
          onClick={send}
          disabled={busy || !input.trim()}
          className="w-[34px] rounded-md bg-accent text-white disabled:opacity-50"
        >
          ➤
        </button>
      </div>
    </div>
  );
}

function Bubble({ who, children }: { who: "u" | "a"; children: React.ReactNode }) {
  const base = "max-w-[88%] whitespace-pre-wrap rounded-lg px-3 py-2 text-[13px] leading-[1.45]";
  return who === "u" ? (
    <div className={`${base} self-end bg-accent-weak text-ink`}>{children}</div>
  ) : (
    <div className={`${base} self-start border border-hairline bg-canvas`}>{children}</div>
  );
}
