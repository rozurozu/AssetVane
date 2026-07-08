"use client";

// 判断軌跡ページ（ADR-092・観測層）。track_record（結果の質）と対で「AI が実際にどう判断したか
// （プロセスの質）」を数字で見る。面別サマリ（呼んだ Tool 列・規律充足・ラウンド数・打ち切り率）＋
// 直近の軌跡カード。DB には触れない。データ取得はすべて lib/api 経由（ADR-005）。

import { TurnCard } from "@/components/turns/TurnCard";
import { StatusBlock } from "@/components/ui/StatusBlock";
import { type TurnsSummaryRow, getAdvisorTurns } from "@/lib/api";
import { pct } from "@/lib/format";
import { useApi } from "@/lib/use-api";

// 面バッジのラベル（TurnCard と揃える・サマリ表の行頭）。
const SOURCE_LABEL: Record<string, string> = {
  chat: "チャット",
  nightly: "夜AI",
  reviewer: "経験蒸留",
  profiler: "プロファイル",
  skeptic: "反証",
};

function SummaryRow({ row }: { row: TurnsSummaryRow }) {
  return (
    <tr className="border-hairline-soft border-t">
      <td className="py-1.5 pr-3 font-medium text-[13px]">
        {SOURCE_LABEL[row.source] ?? row.source}
      </td>
      <td className="num py-1.5 pr-3 text-right text-[13px]">{row.n_turns}</td>
      <td className="num py-1.5 pr-3 text-right text-[13px] text-ink-muted">
        {row.avg_rounds != null ? row.avg_rounds.toFixed(1) : "—"}
      </td>
      <td className="num py-1.5 pr-3 text-right text-[13px] text-ink-muted">
        {pct(row.truncated_rate)}
      </td>
      <td className="num py-1.5 pr-3 text-right text-[13px] text-ink-muted">
        {row.n_propose_trade}
      </td>
      <td className="num py-1.5 text-right text-[13px]">
        {row.disciplined_rate != null ? (
          <span className={row.disciplined_rate >= 0.999 ? "text-accent" : "text-warning"}>
            {pct(row.disciplined_rate)}
          </span>
        ) : (
          <span className="text-ink-subtle">—</span>
        )}
      </td>
    </tr>
  );
}

export default function AdvisorTurnsPage() {
  const { data, error, loading } = useApi((signal) => getAdvisorTurns(undefined, signal), []);

  return (
    <>
      <div className="mb-3">
        <div className="font-semibold text-[20px] tracking-[-0.4px]">判断軌跡（Advisor Turns）</div>
        <div className="mt-0.5 text-[12px] text-ink-muted">
          AI が実際にどう判断したかの記録（ADR-092）。呼んだ Tool
          列・規律充足・ラウンド数・打ち切り率。 reply 本文は残さない（ADR-025/029）。/settings
          の「夜AI を今すぐ回す」で増える。
        </div>
      </div>

      <StatusBlock
        loading={loading}
        error={error}
        empty={data != null && data.recent.length === 0}
        className="rounded-lg border border-hairline bg-surface-1 p-3"
        errorHint="backend が起動しているか確認するのだ。"
        emptyText="まだ軌跡がないのだ。/settings の「夜AI を今すぐ回す」か、チャットで LLM を回すと増えるのだ（LLM 面の設定が必要）。"
      >
        <div className="flex flex-col gap-4">
          {/* 面別サマリ（全期間の集計）。 */}
          {data && data.summary.length > 0 && (
            <div className="overflow-x-auto rounded-lg border border-hairline bg-surface-1 p-3">
              <table className="w-full min-w-[520px] text-left">
                <thead>
                  <tr className="text-[11px] text-ink-subtle">
                    <th className="pb-1 font-medium">面</th>
                    <th className="pb-1 text-right font-medium">ターン数</th>
                    <th className="pb-1 text-right font-medium">平均ラウンド</th>
                    <th className="pb-1 text-right font-medium">打ち切り率</th>
                    <th className="pb-1 text-right font-medium">起票ターン</th>
                    <th className="pb-1 text-right font-medium">規律充足率</th>
                  </tr>
                </thead>
                <tbody>
                  {data.summary.map((row) => (
                    <SummaryRow key={row.source} row={row} />
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* 直近の軌跡カード（created_at 降順）。 */}
          <div className="flex flex-col gap-2">
            {data?.recent.map((t) => (
              <TurnCard key={t.id} turn={t} />
            ))}
          </div>
        </div>
      </StatusBlock>
    </>
  );
}
