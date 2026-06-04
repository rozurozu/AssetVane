"use client";

// Journal ページ（screens.md §3・spec §9.4）。
// 投資日記一覧（GET /journal・date 降順）。各エントリに observations 本文＋
// policy_snapshot の差分チップ＋source（nightly/chat）バッジ（ADR-029）。
// DB には触れない。データ取得はすべて lib/api.ts 経由（ADR-005）。

import { StatusBlock } from "@/components/ui/StatusBlock";
import { type JournalEntry, getJournal } from "@/lib/api";
import { useApi } from "@/lib/use-api";

// policy_snapshot（その時点の policy）から差分チップ用の主要値を抜く。
// 数値の整形のみ（計算はしない）。snapshot の形は backend の policy core に準ずる。
function snapshotChips(snapshot: unknown): { label: string; value: string }[] {
  if (snapshot == null || typeof snapshot !== "object") return [];
  const s = snapshot as Record<string, unknown>;
  const core = (s.core && typeof s.core === "object" ? s.core : s) as Record<string, unknown>;
  const chips: { label: string; value: string }[] = [];
  // format.ts の pct は非数を "—" にするためチップ抑止に使えない。ここは null を返す局所版。
  const pctChip = (v: unknown): string | null =>
    typeof v === "number" ? `${(v * 100).toFixed(1)}%` : null;

  if (typeof core.risk_tolerance === "string") {
    chips.push({ label: "リスク", value: core.risk_tolerance });
  }
  if (typeof core.time_horizon === "string") {
    chips.push({ label: "時間軸", value: core.time_horizon });
  }
  const cash = pctChip(core.target_cash_ratio);
  if (cash) chips.push({ label: "現金目標", value: cash });
  const maxPos = pctChip(core.max_position_weight);
  if (maxPos) chips.push({ label: "1銘柄上限", value: maxPos });
  const ret = pctChip(core.target_return);
  if (ret) chips.push({ label: "目標リターン", value: ret });
  if (core.no_leverage === true) chips.push({ label: "レバレッジ", value: "不可" });
  if (Array.isArray(core.exclusions) && core.exclusions.length > 0) {
    chips.push({ label: "除外", value: `${core.exclusions.length}件` });
  }
  return chips;
}

// proposed_policy_change（{field, from, to, reason}）を 1 行に整形する。
function changeSummary(change: unknown): string | null {
  if (change == null || typeof change !== "object") return null;
  const c = change as Record<string, unknown>;
  if (typeof c.field !== "string") return null;
  const from = c.from != null ? String(c.from) : "—";
  const to = c.to != null ? String(c.to) : "—";
  return `${c.field}: ${from} → ${to}`;
}

function Entry({ entry }: { entry: JournalEntry }) {
  const chips = snapshotChips(entry.policy_snapshot);
  const change = changeSummary(entry.proposed_policy_change);

  return (
    <section className="rounded-lg border border-hairline bg-surface-1">
      <div className="flex items-center gap-2 border-hairline border-b px-3 py-2">
        <span className="num font-semibold text-[13px]">{entry.date}</span>
        <span
          className={`rounded-sm px-1.5 py-0.5 font-medium text-[11px] ${
            entry.source === "nightly" ? "bg-accent-weak text-accent" : "bg-surface-2 text-info"
          }`}
        >
          {entry.source === "nightly" ? "夜の分析" : "チャット要約"}
        </span>
        {entry.llm_model && (
          <span className="num ml-auto text-[11px] text-ink-subtle">{entry.llm_model}</span>
        )}
      </div>
      <div className="space-y-2 p-3">
        {entry.observations && (
          <p className="text-[13px] text-ink-muted leading-[1.55]">{entry.observations}</p>
        )}
        {entry.proposal && (
          <div className="rounded-md border border-hairline border-l-2 border-l-accent bg-canvas px-3 py-2 text-[13px] text-ink leading-[1.5]">
            {entry.proposal}
          </div>
        )}
        {change && (
          <div className="text-[12px] text-warning">
            方針変更案 ・ <span className="num">{change}</span>
          </div>
        )}
        {chips.length > 0 && (
          <div className="flex flex-wrap gap-1.5 border-hairline-soft border-t pt-2">
            {chips.map((c) => (
              <span
                key={c.label}
                className="rounded-sm bg-surface-2 px-1.5 py-0.5 text-[11px] text-ink-muted"
              >
                {c.label} <span className="num text-ink">{c.value}</span>
              </span>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

export default function JournalPage() {
  const { data, error, loading } = useApi((signal) => getJournal(undefined, undefined, signal), []);

  return (
    <>
      <div className="mb-3">
        <div className="font-semibold text-[20px] tracking-[-0.4px]">投資日記（Journal）</div>
        <div className="mt-0.5 text-[12px] text-ink-muted">
          夜の分析AI の所見と、チャット要約の昇格（ADR-029）。date 降順。
        </div>
      </div>

      <StatusBlock
        loading={loading}
        error={error}
        empty={data?.entries.length === 0}
        className="rounded-lg border border-hairline bg-surface-1 p-3"
        errorHint="backend が起動しているか確認するのだ。"
        emptyText="まだ日記がないのだ。夜間バッチ（POST /batch/run）か、チャットの「journal に残す」で増えるのだ。"
      >
        <div className="flex flex-col gap-2">
          {data?.entries.map((e) => (
            <Entry key={e.id} entry={e} />
          ))}
        </div>
      </StatusBlock>
    </>
  );
}
