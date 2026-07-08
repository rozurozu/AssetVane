"use client";

// AI 判断軌跡カード（ADR-092・観測層）。1 LLM ターン＝1 カード。
// 面バッジ・規律充足バッジ・打ち切りバッジ・Tool 軌跡（名前の連なりで要約表示＝args は生表示しない
// ＝ADR-025）を出す。ProposalCard のバッジ作法を手本。DB には触れない（ADR-005）。

import type { TurnItem } from "@/lib/api";

// 面バッジ（分類なので good/bad ではなく中立トークン。nightly=accent で軸1 を目立たせる）。
const SOURCE_LABEL: Record<string, string> = {
  chat: "チャット",
  nightly: "夜AI",
  reviewer: "経験蒸留",
  profiler: "プロファイル",
  skeptic: "反証",
};
const SOURCE_CLS: Record<string, string> = {
  chat: "bg-surface-2 text-info",
  nightly: "bg-accent-weak text-accent",
  reviewer: "bg-surface-2 text-ink-muted",
  profiler: "bg-surface-2 text-ink-muted",
  skeptic: "bg-surface-2 text-warning",
};

// Tool 名の要約表示ラベル（未知はキーそのまま）。起票/日記/選別など「効いた」Tool を目立たせる。
const TOOL_LABEL: Record<string, string> = {
  propose_trade: "売買提案",
  submit_journal: "日記",
  submit_notable_stocks: "注目選別",
  propose_card: "カード下書き",
  submit_refutation: "反証注記",
  propose_profile_note: "傾向メモ",
  propose_watchlist: "ウォッチ候補",
};

function isoTime(iso: string | null): string {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  return new Date(t).toLocaleString("ja-JP", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function TurnCard({ turn }: { turn: TurnItem }) {
  const sourceLabel = SOURCE_LABEL[turn.source] ?? turn.source;
  const sourceCls = SOURCE_CLS[turn.source] ?? "bg-surface-2 text-ink-muted";

  return (
    <section className="rounded-lg border border-hairline bg-surface-1">
      <div className="flex flex-wrap items-center gap-2 border-hairline border-b px-3 py-2">
        <span className={`rounded-sm px-1.5 py-0.5 font-medium text-[11px] ${sourceCls}`}>
          {sourceLabel}
        </span>
        <span className="num text-[12px] text-ink-muted">{isoTime(turn.created_at)}</span>
        <span className="text-[11px] text-ink-subtle">{turn.n_rounds} ラウンド</span>
        {turn.truncated && (
          <span className="rounded-sm bg-down-weak px-1.5 py-0.5 font-medium text-[11px] text-down">
            打ち切り
          </span>
        )}
        {/* 規律充足バッジ（起票ターンのみ）。全備=accent／欠落=warning（good/bad ではなく規律の質）。 */}
        {turn.called_propose_trade &&
          (turn.propose_trade_disciplined ? (
            <span className="rounded-sm bg-accent-weak px-1.5 py-0.5 font-medium text-[11px] text-accent">
              規律 全備
            </span>
          ) : (
            <span className="rounded-sm bg-surface-2 px-1.5 py-0.5 font-medium text-[11px] text-warning">
              規律 欠落
            </span>
          ))}
        {turn.model && (
          <span className="num ml-auto text-[11px] text-ink-subtle">{turn.model}</span>
        )}
      </div>

      <div className="space-y-2 p-3">
        {/* Tool 軌跡（名前の連なり＝要約表示・args は出さない・ADR-025）。 */}
        {turn.tool_sequence.length > 0 ? (
          <div className="flex flex-wrap items-center gap-1">
            {turn.tool_sequence.map((c, i) => {
              const label = TOOL_LABEL[c.name] ?? c.name;
              const emphasized = c.name in TOOL_LABEL;
              return (
                <span key={`${c.name}-${i}`} className="flex items-center gap-1">
                  {i > 0 && <span className="text-[11px] text-ink-subtle">→</span>}
                  <span
                    className={`rounded-sm px-1.5 py-0.5 text-[11px] ${
                      emphasized ? "bg-accent-weak text-accent" : "bg-surface-2 text-ink-muted"
                    }`}
                  >
                    {label}
                  </span>
                </span>
              );
            })}
          </div>
        ) : (
          <p className="text-[12px] text-ink-subtle">Tool 呼び出しなし（そのまま応答）。</p>
        )}
      </div>
    </section>
  );
}
