"use client";

// 相談チャットAI（軸2）の常駐フローティング UI（ADR-024・screens.md §4・spec §9.1）。
// root layout に置きページ遷移で会話を保持。Tool 接続済みサーバへ毎ターン全 messages を送る（ステートレス）。
// - 画面コンテキスト送信（ADR-025）: usePathname → ChatContext（数値は載せない）。
// - tool_runs 可視化（screens.md §4）: AI が呼んだ Tool をチップ表示（結果値は出さない）。
// - 会話の永続（U-6/ADR-029）: localStorage（同一ブラウザで永続）。サーバはステートレス維持。
// - journal 昇格（ADR-029）: 「この会話を要約して journal に残す」アクション（ユーザー承認後のみ）。
// - ドラッグ／リサイズ／最小化: 依存を増やさず自前 pointer ハンドル（OPEN-H）。

import { onOpenAdvisorChat } from "@/lib/advisor-bus";
import { type ChatMessage, type ChatResponse, type ToolRun, sendChat } from "@/lib/api";
import { contextLabel, pathnameToContext } from "@/lib/chat-context";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

// assistant バブルは tool_runs を併せ持つ（user は持たない）。
type Msg = ChatMessage & { tool_runs?: ToolRun[] };

// localStorage キー（同一ブラウザで永続・ADR-029）。サイズ/位置は別キー。
const LS_MESSAGES = "advisor.messages.v1";
const LS_RECT = "advisor.rect.v1";

const MIN_W = 320;
const MIN_H = 360;

type Rect = { w: number; h: number };

export function AdvisorChat() {
  const pathname = usePathname();
  const [open, setOpen] = useState(true);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
  const [rect, setRect] = useState<Rect>({ w: 360, h: 520 });
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [hydrated, setHydrated] = useState(false);
  const drag = useRef<{ x: number; y: number; left: number; top: number } | null>(null);
  const resize = useRef<{ x: number; y: number; w: number; h: number } | null>(null);
  const chatRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  // localStorage から会話・窓サイズを復元（マウント後 1 回・ADR-029）。
  useEffect(() => {
    try {
      const m = localStorage.getItem(LS_MESSAGES);
      if (m) setMessages(JSON.parse(m) as Msg[]);
      const r = localStorage.getItem(LS_RECT);
      if (r) setRect(JSON.parse(r) as Rect);
    } catch {
      // 壊れた JSON は無視して空から始める。
    }
    setHydrated(true);
  }, []);

  // 会話が変わるたび localStorage へ保存（同一ブラウザで永続）。
  useEffect(() => {
    if (!hydrated) return;
    try {
      localStorage.setItem(LS_MESSAGES, JSON.stringify(messages));
    } catch {}
  }, [messages, hydrated]);

  // 窓サイズの永続。
  useEffect(() => {
    if (!hydrated) return;
    try {
      localStorage.setItem(LS_RECT, JSON.stringify(rect));
    } catch {}
  }, [rect, hydrated]);

  // nav「Advisor」等からの open 要求を購読（OPEN-I・advisor-bus）。
  useEffect(() => onOpenAdvisorChat(() => setOpen(true)), []);

  // 新しいメッセージが来たら最下部へ。
  // biome-ignore lint/correctness/useExhaustiveDependencies: messages 変化時に末尾へスクロール
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages.length, busy]);

  // 1 ターン送信（context を載せる・数値は載せない＝ADR-025）。
  // 応答 ChatResponse を return する（promoteToJournal が journal_id を読むため・ADR-029）。
  // 送信できなかった/失敗した場合は null。既存呼び出し元（send 等）は戻り値を無視しても壊れない。
  const sendText = useCallback(
    async (text: string): Promise<ChatResponse | null> => {
      const t = text.trim();
      if (!t || busy) return null;
      const next: Msg[] = [...messages, { role: "user", content: t }];
      setMessages(next);
      setInput("");
      setBusy(true);
      try {
        // 送信時の context は messages（user/assistant のみ）と分離して載せる。
        const payloadMessages: ChatMessage[] = next.map((m) => ({
          role: m.role,
          content: m.content,
        }));
        const data = await sendChat({
          messages: payloadMessages,
          context: pathnameToContext(pathname),
        });
        setMessages((m) => [
          ...m,
          { role: "assistant", content: data.reply, tool_runs: data.tool_runs },
        ]);
        return data;
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        setMessages((m) => [
          ...m,
          { role: "assistant", content: `⚠ Advisor に繋がらなかった: ${msg}` },
        ]);
        return null;
      } finally {
        setBusy(false);
      }
    },
    [busy, messages, pathname],
  );

  const send = useCallback(() => sendText(input), [sendText, input]);

  // 会話を journal に残す（承認後のみ＝黙って自動保存しない・ADR-029/ADR-014）。
  // 定型文を /chat に送り、AI が submit_journal Tool を呼べば journal に残る。
  // 応答 ChatResponse.journal_id（number=成功 / null=未昇格）を読んで成否をインラインで表示する。
  const promoteToJournal = useCallback(async () => {
    if (busy || messages.length === 0) return;
    if (!window.confirm("この会話を要約して journal に残すのだ？（AI が要約を作る）")) return;
    const data = await sendText("この会話を要約して投資日記（journal）に残してほしいのだ。");
    if (!data) return; // 送信失敗時は sendText 内で⚠バブルを出済みなので何もしない。
    const ok = typeof data.journal_id === "number";
    setMessages((m) => [
      ...m,
      {
        role: "assistant",
        content: ok
          ? "📓 この会話を投資日記に残したのだ（Journal 画面で確認できる）。"
          : "⚠ 投資日記に残せなかった（所見が空、またはAIが要約を提出しなかった）。",
      },
    ]);
  }, [busy, messages.length, sendText]);

  const clearChat = useCallback(() => {
    if (busy) return;
    if (!window.confirm("この端末の会話履歴を消すのだ？")) return;
    setMessages([]);
  }, [busy]);

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

  const ctxLabel = contextLabel(pathnameToContext(pathname));

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

      {/* 画面コンテキスト（指示語解決のヒント・数値は渡さない＝ADR-025）。
          usePathname → ChatContext を実値で表示し、/chat の body に context として送る。 */}
      <div className="border-hairline-soft border-b bg-canvas px-3 py-1.5 text-[11px] text-ink-muted">
        📍 見ているページ: <b className="text-accent">{ctxLabel}</b> ・ 指示語はこの文脈で解決
      </div>

      <div ref={scrollRef} className="flex flex-1 flex-col gap-2.5 overflow-auto p-3">
        {messages.length === 0 && (
          <div className="m-auto px-4 text-center text-[12px] text-ink-subtle leading-[1.6]">
            投資方針や銘柄の考え方を相談できるのだ。
            <br />
            数値は AI が Tool で取り直して答える（ADR-014）。
          </div>
        )}
        {messages.map((m, i) => (
          // biome-ignore lint/suspicious/noArrayIndexKey: 追記のみで並びは不変
          <Bubble key={i} who={m.role === "user" ? "u" : "a"} toolRuns={m.tool_runs}>
            {m.content}
          </Bubble>
        ))}
        {busy && (
          <Bubble who="a">
            <span className="text-ink-subtle">考え中…</span>
          </Bubble>
        )}
      </div>

      {/* 会話アクション：journal 昇格（承認後のみ）／履歴クリア。 */}
      {messages.length > 0 && (
        <div className="flex items-center gap-2 border-hairline-soft border-t px-3 py-1.5">
          <button
            type="button"
            onClick={promoteToJournal}
            disabled={busy}
            className="rounded-md border border-hairline px-2 py-1 text-[11px] text-ink-muted hover:bg-surface-2 hover:text-ink disabled:opacity-50"
          >
            📓 この会話を journal に残す
          </button>
          <button
            type="button"
            onClick={clearChat}
            disabled={busy}
            className="ml-auto rounded-md px-2 py-1 text-[11px] text-ink-subtle hover:text-down disabled:opacity-50"
          >
            履歴を消す
          </button>
        </div>
      )}

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

function Bubble({
  who,
  toolRuns,
  children,
}: {
  who: "u" | "a";
  toolRuns?: ToolRun[];
  children: React.ReactNode;
}) {
  const base = "max-w-[88%] whitespace-pre-wrap rounded-lg px-3 py-2 text-[13px] leading-[1.45]";
  if (who === "u") {
    return <div className={`${base} self-end bg-accent-weak text-ink`}>{children}</div>;
  }
  return (
    <div className="flex max-w-[88%] flex-col gap-1 self-start">
      {/* tool_runs 可視化（screens.md §4）。呼んだ Tool 名のみ・結果値は出さない（ADR-025）。 */}
      {toolRuns && toolRuns.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {toolRuns.map((t, i) => (
            <span
              key={`${t.name}-${i}`}
              className="rounded-sm bg-surface-2 px-1.5 py-0.5 text-[11px] text-ink-muted"
            >
              ⚙ {t.name} 実行
            </span>
          ))}
        </div>
      )}
      <div className={`${base} border border-hairline bg-canvas`}>{children}</div>
    </div>
  );
}
