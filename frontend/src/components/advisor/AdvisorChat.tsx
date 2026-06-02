"use client";

import { useCallback, useRef, useState } from "react";

// 相談チャットAI（軸2）の常駐フローティング UI（ADR-024）。
// 今回はダミー会話＋開閉＋ドラッグのみ（配線は Phase 3）。Tool 実行の可視化も見た目だけ。
// 会話・窓位置の永続（localStorage）と /chat 配線は実装時に足す（screens.md §4）。
export function AdvisorChat() {
  const [open, setOpen] = useState(true);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
  const drag = useRef<{ x: number; y: number; left: number; top: number } | null>(null);
  const chatRef = useRef<HTMLDivElement>(null);

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

      {/* 画面コンテキスト（指示語解決のヒント・数値は渡さない＝ADR-025） */}
      <div className="border-hairline-soft border-b bg-canvas px-3 py-1.5 text-[11px] text-ink-muted">
        📍 見ているページ: <b className="text-accent">Dashboard</b> ・ 画面の数字を文脈に相談できる
      </div>

      <div className="flex flex-1 flex-col gap-2.5 overflow-auto p-3">
        <Bubble who="a">
          おかえり。今朝の状況だと
          <strong className="font-semibold text-ink">半導体の集中度が方針と擦れてる</strong>
          （最大 <span className="num">18.2%</span> / 上限 <span className="num">15%</span>
          ）。承認待ちを2件出してる。気になる点ある？
        </Bubble>
        <Bubble who="u">1銘柄上限、上げてもいい？</Bubble>
        <Bubble who="a">
          <span className="mb-1.5 inline-block text-[11px] text-accent">
            ⚙ get_asset_overview / get_signals
          </span>
          <br />
          事実だと最大比率は<span className="num text-up"> 18.2%</span>（上限超過）、6920 は
          momentum <span className="num">0.88</span>・出来高<span className="num">3.4倍</span>
          。上げるなら
          <strong className="font-semibold text-ink">現金25%維持を条件</strong>
          に。トレードオフは集中リスク。上げ幅は <span className="num">20%</span>{" "}
          が妥当。レバレッジ不可は維持。
        </Bubble>
      </div>

      <div className="flex gap-2 border-hairline border-t px-3 py-2.5">
        <input
          placeholder="この画面について質問…（例: 4063 を調査して）"
          className="flex-1 rounded-md border border-hairline bg-canvas px-2.5 py-1.5 text-[13px] text-ink outline-none focus:border-accent"
        />
        <button type="button" className="w-[34px] rounded-md bg-accent text-white">
          ➤
        </button>
      </div>
    </div>
  );
}

function Bubble({ who, children }: { who: "u" | "a"; children: React.ReactNode }) {
  const base = "max-w-[88%] rounded-lg px-3 py-2 text-[13px] leading-[1.45]";
  return who === "u" ? (
    <div className={`${base} self-end bg-accent-weak text-ink`}>{children}</div>
  ) : (
    <div className={`${base} self-start border border-hairline bg-canvas`}>{children}</div>
  );
}
