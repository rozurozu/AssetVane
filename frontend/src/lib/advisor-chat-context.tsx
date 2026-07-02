"use client";

// 相談チャット（軸2）の会話状態を共有する Context（ADR-024/029・ADR-065）。
// 常駐フローティング（AdvisorChat）と専用ページ（/advisor）が「同一の会話」を見るために、
// root layout 直下に Provider を置き、メッセージ・送信・journal 昇格をここに集約する。
// - 会話の永続（U-6/ADR-029）: localStorage（同一ブラウザで永続）。サーバはステートレス維持。
// - 画面コンテキスト送信（ADR-025）: usePathname → ChatContext（数値は載せない）。送信時に解決。
// - カード起票フィードバック（ADR-065）: 応答 ChatResponse.card_ids を lastCardIds に保持し、
//   表示側（ChatConversation）が /cards への導線をインライン表示する（journal_id と同型）。
// ※ frontend-component-pattern「常駐フローティングは自前で会話状態を持つ」例外を、ページ共有の
//    ため Context へ持ち上げたもの（描画は AdvisorChat / ChatConversation が担う）。

import {
  type ChatMessage,
  type ChatResponse,
  type ToolRun,
  type WatchlistCandidate,
  sendChat,
} from "@/lib/api";
import { pathnameToContext } from "@/lib/chat-context";
import { usePathname } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";

// assistant バブルは tool_runs とウォッチ候補（ADR-080）を併せ持つ（user は持たない）。
// watchlist_candidates はメッセージに括り付けて localStorage 永続する（遡っても選べる・card_ids の
// ような最新ターンだけの transient state にしない）。
export type Msg = ChatMessage & {
  tool_runs?: ToolRun[];
  watchlist_candidates?: WatchlistCandidate[];
};

// localStorage キー（同一ブラウザで永続・ADR-029）。窓サイズ/位置は AdvisorChat 側の別キー。
const LS_MESSAGES = "advisor.messages.v1";

type AdvisorChatValue = {
  messages: Msg[];
  busy: boolean;
  lastCardIds: number[]; // 直近ターンで propose_card が起票した draft の id（ADR-065）
  sendText: (text: string) => Promise<ChatResponse | null>;
  // 送信中キャンセル（ADR-072）: 進行中の fetch を abort し、直前 user 発話をスレッドから取り除く。
  // 戻り値は取り除いた発話の文面（呼び出し側が入力欄に戻して編集再送できる・null=中止対象なし）。
  cancel: () => string | null;
  promoteToJournal: () => Promise<void>;
  clearChat: () => void;
};

const AdvisorChatContext = createContext<AdvisorChatValue | null>(null);

export function AdvisorChatProvider({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [messages, setMessages] = useState<Msg[]>([]);
  const [busy, setBusy] = useState(false);
  const [lastCardIds, setLastCardIds] = useState<number[]>([]);
  const [hydrated, setHydrated] = useState(false);
  // 送信中キャンセル（ADR-072）。abortRef=進行中送信の AbortController、pendingUserRef=その user 発話。
  const abortRef = useRef<AbortController | null>(null);
  const pendingUserRef = useRef<string | null>(null);

  // localStorage から会話を復元（マウント後 1 回・ADR-029）。
  useEffect(() => {
    try {
      const m = localStorage.getItem(LS_MESSAGES);
      if (m) setMessages(JSON.parse(m) as Msg[]);
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

  // 1 ターン送信（context を載せる・数値は載せない＝ADR-025）。応答 ChatResponse を return し、
  // promoteToJournal が journal_id を読む（ADR-029）。送信不可/失敗時は null。
  const sendText = useCallback(
    async (text: string): Promise<ChatResponse | null> => {
      const t = text.trim();
      if (!t || busy) return null;
      const next: Msg[] = [...messages, { role: "user", content: t }];
      setMessages(next);
      setLastCardIds([]); // 新しい送信で前ターンの起票通知をクリア（最新ターンのみ表示）
      setBusy(true);
      // 送信中キャンセル（ADR-072）: この送信専用の AbortController と、戻す用の user 発話を保持。
      const controller = new AbortController();
      abortRef.current = controller;
      pendingUserRef.current = t;
      try {
        const payloadMessages: ChatMessage[] = next.map((m) => ({
          role: m.role,
          content: m.content,
        }));
        const data = await sendChat(
          {
            messages: payloadMessages,
            context: pathnameToContext(pathname),
          },
          controller.signal,
        );
        setMessages((m) => [
          ...m,
          {
            role: "assistant",
            content: data.reply,
            tool_runs: data.tool_runs,
            // ウォッチ候補をこの assistant メッセージに括り付ける（localStorage 永続・ADR-080）。
            watchlist_candidates: data.watchlist_candidates,
          },
        ]);
        // propose_card が draft を起票したら id を保持（/cards 導線のインライン表示・ADR-065）。
        if (data.card_ids && data.card_ids.length > 0) setLastCardIds(data.card_ids);
        return data;
      } catch (e) {
        // ユーザーが中止したとき（cancel が abort 済み）は⚠バブルを出さない。発話の取り除きと
        // 入力欄への復元は cancel が済ませている（ADR-072）。
        if (controller.signal.aborted) return null;
        const msg = e instanceof Error ? e.message : String(e);
        setMessages((m) => [
          ...m,
          { role: "assistant", content: `⚠ Advisor に繋がらなかった: ${msg}` },
        ]);
        return null;
      } finally {
        if (abortRef.current === controller) abortRef.current = null;
        setBusy(false);
      }
    },
    [busy, messages, pathname],
  );

  // 送信中キャンセル（ADR-072）: 進行中 fetch を abort し、直前の user 発話をスレッドから取り除く。
  // 取り除いた文面を返し、呼び出し側（ChatConversation）が入力欄に戻して編集再送できるようにする。
  const cancel = useCallback((): string | null => {
    const c = abortRef.current;
    if (!c) return null; // 進行中送信なし（既に応答到達等）は何もしない
    const restored = pendingUserRef.current;
    // 末尾が user 発話のとき（応答未着）だけ取り除く。競合で応答が先着していたら消さない。
    setMessages((m) => (m[m.length - 1]?.role === "user" ? m.slice(0, -1) : m));
    c.abort(); // fetch 中断 → sendText の catch が signal.aborted で握る（⚠バブルを出さない）
    return restored;
  }, []);

  // 会話を journal に残す（承認後のみ＝黙って自動保存しない・ADR-029/014）。
  // 応答 ChatResponse.journal_id（number=成功 / null=未昇格）を読んで成否をインライン表示する。
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
    setLastCardIds([]);
  }, [busy]);

  return (
    <AdvisorChatContext.Provider
      value={{ messages, busy, lastCardIds, sendText, cancel, promoteToJournal, clearChat }}
    >
      {children}
    </AdvisorChatContext.Provider>
  );
}

/** 会話状態フック。AdvisorChatProvider の内側（root layout 配下＝全ページ）で使う。 */
export function useAdvisorChat(): AdvisorChatValue {
  const v = useContext(AdvisorChatContext);
  if (!v) throw new Error("useAdvisorChat は AdvisorChatProvider の内側で使うのだ");
  return v;
}
