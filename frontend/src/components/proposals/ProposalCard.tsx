"use client";

// 提案カード（screens.md §3・spec §9.3）。
// kind バッジ（POLICY=accent / BUY=up 系 / SELL=down 系 / REBALANCE=info）。
// 承認/却下（approveProposal/rejectProposal）。depends_on が未承認なら承認ボタン無効＋注記。
// 承認は約定を起こさない（status 遷移のみ＝ADR-001/019）。DB には触れない（ADR-005）。

import type { Proposal } from "@/lib/api";

type Props = {
  proposal: Proposal;
  // depends_on が指す提案が approved 済みか（承認順制御・spec §9.3）。
  dependencyMet: boolean;
  busy: boolean;
  onApprove: (id: number) => void;
  onReject: (id: number) => void;
};

const KIND_LABEL: Record<Proposal["kind"], string> = {
  policy_change: "POLICY",
  buy: "BUY",
  sell: "SELL",
  rebalance: "REBALANCE",
};

const KIND_CLS: Record<Proposal["kind"], string> = {
  policy_change: "bg-accent-weak text-accent",
  buy: "bg-up-weak text-up",
  sell: "bg-down-weak text-down",
  rebalance: "bg-surface-2 text-info",
};

// body（kind 依存 JSON）を簡易に要約表示する（厳密整形は backend 確定後でよい）。
// buy/sell（ADR-052）は body={code, company_name, market} なので「会社名（code・market）」に整形。
function bodySummary(body: unknown, kind: Proposal["kind"]): string | null {
  if (body == null) return null;
  if (typeof body === "string") return body;
  if ((kind === "buy" || kind === "sell") && typeof body === "object") {
    const b = body as { code?: string; company_name?: string; market?: string };
    if (b.code) {
      const name = b.company_name || b.code;
      const meta = [b.code, b.market].filter(Boolean).join("・");
      return `${name}（${meta}）`;
    }
  }
  try {
    return JSON.stringify(body);
  } catch {
    return null;
  }
}

// buy/sell の body に載る判断属性（ADR-084）。承認者が確信度・前提崩れ条件を見て判断できるよう表示。
type Judgment = { conviction?: string; invalidation?: string; catalyst?: string };
function bodyJudgment(body: unknown, kind: Proposal["kind"]): Judgment {
  if ((kind === "buy" || kind === "sell") && body != null && typeof body === "object") {
    const b = body as Judgment;
    return {
      conviction: typeof b.conviction === "string" ? b.conviction : undefined,
      invalidation: typeof b.invalidation === "string" ? b.invalidation : undefined,
      catalyst: typeof b.catalyst === "string" ? b.catalyst : undefined,
    };
  }
  return {};
}

// 確信度バッジ（good/bad ではなく確からしさなので up/down は使わず accent/中立トークンで表す）。
const CONVICTION_LABEL: Record<string, string> = {
  high: "確信度 高",
  medium: "確信度 中",
  low: "確信度 低",
};
const CONVICTION_CLS: Record<string, string> = {
  high: "bg-accent-weak text-accent",
  medium: "bg-surface-2 text-ink-muted",
  low: "bg-surface-2 text-ink-subtle",
};

export function ProposalCard({ proposal, dependencyMet, busy, onApprove, onReject }: Props) {
  const p = proposal;
  const isPending = p.status === "pending";
  // 依存提案が未承認なら承認できない（承認順制御・決定4）。
  const approveDisabled = busy || !dependencyMet;
  const body = bodySummary(p.body, p.kind);
  const j = bodyJudgment(p.body, p.kind);

  return (
    <div className="rounded-lg border border-hairline bg-surface-1 p-3">
      <div className="flex items-center gap-2">
        <span className={`rounded-sm px-1.5 py-0.5 font-medium text-[12px] ${KIND_CLS[p.kind]}`}>
          {KIND_LABEL[p.kind]}
        </span>
        {body && <span className="font-semibold text-[13px]">{body}</span>}
        {j.conviction && CONVICTION_LABEL[j.conviction] && (
          <span
            className={`rounded-sm px-1.5 py-0.5 font-medium text-[11px] ${
              CONVICTION_CLS[j.conviction] ?? "bg-surface-2 text-ink-subtle"
            }`}
          >
            {CONVICTION_LABEL[j.conviction]}
          </span>
        )}
        <span className="num ml-auto text-[11px] text-ink-subtle">
          #{p.id} ・ {p.created_date}
        </span>
      </div>

      {p.rationale && (
        <div className="my-1.5 text-[13px] text-ink-muted leading-[1.45]">{p.rationale}</div>
      )}

      {/* 判断属性（ADR-084）＝AI が示した catalyst・前提崩れ条件を承認判断の材料に見せる。 */}
      {(j.catalyst || j.invalidation) && (
        <div className="mb-1.5 space-y-0.5 text-[11px] text-ink-subtle leading-[1.4]">
          {j.catalyst && <div>catalyst: {j.catalyst}</div>}
          {j.invalidation && <div>前提崩れ: {j.invalidation}</div>}
        </div>
      )}

      {/* 依存注記（承認順制御）。 */}
      {p.depends_on != null && (
        <div className={`mb-1.5 text-[11px] ${dependencyMet ? "text-ink-subtle" : "text-warning"}`}>
          {dependencyMet
            ? `提案 #${p.depends_on} の承認済み（依存解消）`
            : `提案 #${p.depends_on} の承認が前提（先に承認するのだ）`}
        </div>
      )}

      {isPending ? (
        <div className="flex gap-1.5">
          <button
            type="button"
            onClick={() => onApprove(p.id)}
            disabled={approveDisabled}
            className="rounded-md border border-accent bg-accent px-3 py-1.5 font-medium text-[13px] text-white disabled:opacity-40"
          >
            承認
          </button>
          <button
            type="button"
            onClick={() => onReject(p.id)}
            disabled={busy}
            className="rounded-md border border-hairline bg-surface-2 px-3 py-1.5 font-medium text-[13px] disabled:opacity-50"
          >
            却下
          </button>
          {/* 承認は発注しない注記（ADR-001/019）。 */}
          {(p.kind === "buy" || p.kind === "sell") && (
            <span className="self-center text-[11px] text-ink-subtle">
              ※承認しても発注はしない（約定後に手入力）
            </span>
          )}
        </div>
      ) : (
        <div className="flex items-center gap-2 text-[12px]">
          <span
            className={`rounded-sm px-1.5 py-0.5 font-medium ${
              p.status === "approved" ? "bg-up-weak text-up" : "bg-down-weak text-down"
            }`}
          >
            {p.status === "approved" ? "承認済み" : "却下"}
          </span>
          {p.resolved_at && (
            <span className="num text-ink-subtle">{p.resolved_at.slice(0, 10)}</span>
          )}
          {p.outcome && <span className="text-ink-muted">{p.outcome}</span>}
        </div>
      )}
    </div>
  );
}
