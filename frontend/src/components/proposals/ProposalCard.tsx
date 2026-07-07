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

// buy/sell の body に載る判断属性（ADR-084・想定保有期間は ADR-091）。承認者が確信度・時間軸・
// 前提崩れ条件を見て判断できるよう表示。
type Judgment = {
  conviction?: string;
  invalidation?: string;
  catalyst?: string;
  horizon?: string;
};
function bodyJudgment(body: unknown, kind: Proposal["kind"]): Judgment {
  if ((kind === "buy" || kind === "sell") && body != null && typeof body === "object") {
    const b = body as Judgment;
    return {
      conviction: typeof b.conviction === "string" ? b.conviction : undefined,
      invalidation: typeof b.invalidation === "string" ? b.invalidation : undefined,
      catalyst: typeof b.catalyst === "string" ? b.catalyst : undefined,
      horizon: typeof b.horizon === "string" ? b.horizon : undefined,
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

// 想定保有期間バッジ（ADR-091）。時間軸なので good/bad ではなく中立の info トークンで表す。
const HORIZON_LABEL: Record<string, string> = {
  short: "短期",
  medium: "中期",
  long: "長期",
};
const HORIZON_CLS = "bg-surface-2 text-info";

// buy/sell の body に載る skeptic 反証（ADR-086）。夜バッチの独立面が提案を反証した注記。
type Skeptic = { verdict?: string; refutation?: string; reviewed_at?: string };
function bodySkeptic(body: unknown, kind: Proposal["kind"]): Skeptic | null {
  if ((kind === "buy" || kind === "sell") && body != null && typeof body === "object") {
    const b = body as { skeptic?: unknown };
    if (b.skeptic != null && typeof b.skeptic === "object") {
      const s = b.skeptic as Skeptic;
      if (typeof s.refutation === "string" && s.refutation) return s;
    }
  }
  return null;
}

// 反証 verdict バッジ（holds=筋が通る/weak=論拠が弱い/fragile=前提が脆い）。深刻度で色を強める。
const VERDICT_LABEL: Record<string, string> = {
  holds: "反証: 筋は通る",
  weak: "反証: 論拠が弱い",
  fragile: "反証: 前提が脆い",
};
const VERDICT_CLS: Record<string, string> = {
  holds: "bg-surface-2 text-ink-muted",
  weak: "bg-surface-2 text-warning",
  fragile: "bg-down-weak text-down",
};

export function ProposalCard({ proposal, dependencyMet, busy, onApprove, onReject }: Props) {
  const p = proposal;
  const isPending = p.status === "pending";
  // 依存提案が未承認なら承認できない（承認順制御・決定4）。
  const approveDisabled = busy || !dependencyMet;
  const body = bodySummary(p.body, p.kind);
  const j = bodyJudgment(p.body, p.kind);
  const skeptic = bodySkeptic(p.body, p.kind);

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
        {j.horizon && HORIZON_LABEL[j.horizon] && (
          <span className={`rounded-sm px-1.5 py-0.5 font-medium text-[11px] ${HORIZON_CLS}`}>
            {HORIZON_LABEL[j.horizon]}
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

      {/* 提案前 red-team 反証（ADR-086）＝独立面の反証を承認判断の材料に見せる（自動却下しない）。 */}
      {skeptic && (
        <div className="mb-1.5 rounded-md border border-hairline bg-surface-2 p-2">
          <div className="flex items-center gap-2">
            {skeptic.verdict && (
              <span
                className={`rounded-sm px-1.5 py-0.5 font-medium text-[11px] ${
                  VERDICT_CLS[skeptic.verdict] ?? "bg-surface-2 text-ink-muted"
                }`}
              >
                {VERDICT_LABEL[skeptic.verdict] ?? "反証"}
              </span>
            )}
            <span className="text-[11px] text-ink-subtle">red-team レビュー</span>
          </div>
          <div className="mt-1 text-[12px] text-ink-muted leading-[1.45]">{skeptic.refutation}</div>
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
