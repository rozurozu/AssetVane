"use client";

// 相談チャットの会話本体（軸2・ADR-024/065）。常駐フローティング（AdvisorChat）と
// 専用ページ（/advisor）の両方で使う純粋表示＋入力コンポーネント。会話状態は
// useAdvisorChat（Context）から受け、入力欄とスクロールだけローカルに持つ。
// - 紹介文（空状態）に知識ノート追加できる旨を明記（ADR-065・ユーザー要望）。
// - 起票フィードバック（ADR-065）: lastCardIds があれば /cards への導線を出す（journal と同型）。
// - tool_runs 可視化（screens.md §4）: AI が呼んだ Tool をチップ表示（結果値は出さない＝ADR-025）。

import { useAdvisorChat } from "@/lib/advisor-chat-context";
import type { ToolRun } from "@/lib/api";
import { contextLabel, pathnameToContext } from "@/lib/chat-context";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

type Props = {
  // フローティングは「見ているページ」ヒントを出す。専用ページ（チャットそのもの）は出さない。
  showContextHint?: boolean;
};

export function ChatConversation({ showContextHint = true }: Props) {
  const { messages, busy, lastCardIds, sendText, promoteToJournal, clearChat } = useAdvisorChat();
  const pathname = usePathname();
  const [input, setInput] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  // 新しいメッセージが来たら最下部へ。
  // biome-ignore lint/correctness/useExhaustiveDependencies: messages 変化時に末尾へスクロール
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages.length, busy]);

  const send = useCallback(() => {
    const t = input.trim();
    if (!t || busy) return;
    setInput(""); // 送信が成立するときだけクリア（busy/空のとき入力を消さない）
    void sendText(t);
  }, [input, busy, sendText]);

  const ctxLabel = contextLabel(pathnameToContext(pathname));

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* 画面コンテキスト（指示語解決のヒント・数値は渡さない＝ADR-025）。 */}
      {showContextHint && (
        <div className="border-hairline-soft border-b bg-canvas px-3 py-1.5 text-[11px] text-ink-muted">
          📍 見ているページ: <b className="text-accent">{ctxLabel}</b> ・ 指示語はこの文脈で解決
        </div>
      )}

      <div ref={scrollRef} className="flex flex-1 flex-col gap-2.5 overflow-auto p-3">
        {messages.length === 0 && (
          <div className="m-auto max-w-[420px] px-4 text-center text-[12px] text-ink-subtle leading-[1.6]">
            投資方針や銘柄の考え方を相談できるのだ。
            <br />
            数値は AI が Tool で取り直して答える（ADR-014）。
            <br />
            情報をくれれば、知識ノート（知識カード）も一緒に作れるのだ。
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

      {/* カード起票フィードバック（ADR-065）。直近ターンで draft を起票したら /cards へ誘導。 */}
      {lastCardIds.length > 0 && (
        <div className="flex items-center gap-2 border-hairline-soft border-t bg-surface-2 px-3 py-1.5 text-[11px] text-ink-muted">
          🗂 知識ノートを下書き起票したのだ（{lastCardIds.length} 件）
          <Link
            href="/cards"
            className="ml-auto rounded-md border border-hairline px-2 py-1 text-accent hover:bg-surface-1"
          >
            知識カードで確認・承認
          </Link>
        </div>
      )}

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

      <div className="flex items-end gap-2 border-hairline border-t px-3 py-2.5">
        <textarea
          value={input}
          rows={1}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            // Enter で送信・Shift+Enter で改行（標準）。IME 変換確定中(isComposing)は送信しない。
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              send();
            }
          }}
          disabled={busy}
          placeholder="この画面について質問…（例: 短期で攻める方針を相談したい）"
          className="field-sizing-content max-h-[105px] min-h-[34px] flex-1 resize-none overflow-y-auto rounded-md border border-hairline bg-canvas px-2.5 py-1.5 text-[13px] text-ink leading-[1.4] outline-none focus:border-accent disabled:opacity-60"
        />
        <button
          type="button"
          onClick={send}
          disabled={busy || !input.trim()}
          className="h-[34px] w-[34px] rounded-md bg-accent text-white disabled:opacity-50"
        >
          ➤
        </button>
      </div>
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
