"use client";

// Proposals ページ（screens.md §3・spec §9.3）。
// status タブ（pending/approved/rejected）＋承認/却下。kind バッジ・depends_on 承認順制御。
// 承認は約定を起こさない（status 遷移のみ＝ADR-001/019）。DB には触れない（ADR-005）。

import { ProposalCard } from "@/components/proposals/ProposalCard";
import {
  type Proposal,
  type ProposalsResponse,
  approveProposal,
  getProposals,
  rejectProposal,
} from "@/lib/api";
import { useCallback, useEffect, useState } from "react";

const STATUS_TABS: { key: Proposal["status"]; label: string }[] = [
  { key: "pending", label: "承認待ち" },
  { key: "approved", label: "承認済み" },
  { key: "rejected", label: "却下" },
];

export default function ProposalsPage() {
  const [status, setStatus] = useState<Proposal["status"]>("pending");
  const [data, setData] = useState<ProposalsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  // depends_on の解決判定は全 proposal の approved 集合が要るため、status 横断で別途保持する。
  const [approvedIds, setApprovedIds] = useState<Set<number>>(new Set());

  const load = useCallback(() => {
    setData(null);
    setError(null);
    getProposals(status)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
    // 承認済み集合（依存解消判定用）は常に最新を取得する。
    getProposals("approved")
      .then((r) => setApprovedIds(new Set(r.proposals.map((p) => p.id))))
      .catch(() => {});
  }, [status]);

  useEffect(() => {
    load();
  }, [load]);

  async function resolve(id: number, decision: "approve" | "reject") {
    setBusyId(id);
    try {
      if (decision === "approve") {
        await approveProposal(id);
      } else {
        await rejectProposal(id);
      }
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <>
      <div className="mb-3">
        <div className="font-semibold text-[20px] tracking-[-0.4px]">AI 提案（Proposals）</div>
        <div className="mt-0.5 text-[12px] text-ink-muted">
          夜の分析AI・チャットからの提案を承認/却下する。承認しても発注はしない（提示のみ＝ADR-001）。
        </div>
      </div>

      {/* status 切替タブ（アクティブは surface-2 へ lift＝DESIGN.md）。 */}
      <div className="mb-3 flex gap-1">
        {STATUS_TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => setStatus(t.key)}
            className={`rounded-md px-2.5 py-1 text-[12px] ${
              status === t.key
                ? "bg-surface-2 font-semibold text-ink"
                : "text-ink-muted hover:bg-surface-2 hover:text-ink"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {error && (
        <div className="mb-3 rounded-lg border border-hairline bg-surface-1 p-3 text-[13px] text-down">
          ⚠ 取得に失敗: {error}
          <div className="mt-1 text-[12px] text-ink-subtle">
            backend が起動しているか確認するのだ。
          </div>
        </div>
      )}
      {!error && data === null && (
        <div className="rounded-lg border border-hairline bg-surface-1 p-3 text-[13px] text-ink-subtle">
          読み込み中…
        </div>
      )}
      {!error && data && data.proposals.length === 0 && (
        <div className="rounded-lg border border-hairline bg-surface-1 p-3 text-[13px] text-ink-subtle">
          この状態の提案はないのだ。
        </div>
      )}

      <div className="flex flex-col gap-2">
        {data?.proposals.map((p) => (
          <ProposalCard
            key={p.id}
            proposal={p}
            // depends_on が無い、または依存先が承認済みなら解消（承認可）。
            dependencyMet={p.depends_on == null || approvedIds.has(p.depends_on)}
            busy={busyId === p.id}
            onApprove={(id) => resolve(id, "approve")}
            onReject={(id) => resolve(id, "reject")}
          />
        ))}
      </div>
    </>
  );
}
