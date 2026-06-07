"use client";

// 業種リードラグ widget（Phase 7・GET /lead-lag）。翌日強含み業種の Top N を眺める。
// 自身で fetch する（useApi）が、loading/error/empty の三分岐は StatusBlock に畳む
// （手書き三分岐をしない＝frontend-component-pattern）。整形は lib/format.ts。
// 数値（score/ic/hit_rate）は backend が事前計算した「事実」を読むだけ（AI には計算させない＝ADR-014）。
// meta.is_delayed=true（plan=free か model_as_of が約 3 ヶ月古い）のとき Free 低信頼バナーを出す。

import { Card } from "@/components/ui/Card";
import { DataTable, Td } from "@/components/ui/DataTable";
import { StatusBlock } from "@/components/ui/StatusBlock";
import { getLeadLag } from "@/lib/api";
import { pct } from "@/lib/format";
import { useApi } from "@/lib/use-api";

/** 表示する Top N（翌日強含み業種・score 降順）。 */
const TOP_N = 6;

export function LeadLagWidget() {
  const { data, error, loading } = useApi((signal) => getLeadLag(signal), []);

  const ranking = data?.ranking ?? [];
  const meta = data?.meta;
  const top = ranking.slice(0, TOP_N);

  // モデル品質の小さな注記（ヘッダ右 meta）。IC / hit_rate は 0..1 想定で % 整形。
  const qualityMeta =
    meta != null
      ? `IC ${pct(meta.ic)} ・ Hit ${pct(meta.hit_rate)}${
          meta.model_as_of ? ` ・ ${meta.model_as_of}` : ""
        }`
      : undefined;

  return (
    <Card title="業種リードラグ（翌日強含み）" meta={qualityMeta}>
      {/* Free 低信頼バナー（is_delayed=true のときだけ・目立つ位置＝表の前）。 */}
      {meta?.is_delayed && (
        <div className="mb-3 rounded-md border border-warning border-l-2 border-l-warning bg-canvas px-3 py-2 text-[12px] text-warning leading-[1.5]">
          {meta.plan} プランの 12 週間遅延により、モデル/検証が約 3
          ヶ月古く、翌日予測は実用外なのだ。Light プラン推奨なのだ。
        </div>
      )}

      <StatusBlock
        loading={loading}
        error={error}
        empty={top.length === 0}
        className="py-4 text-center"
        errorHint="backend が起動しているか確認するのだ。"
        emptyText="まだリードラグの算出がないのだ。夜間バッチで生成されるのだ。"
      >
        <DataTable
          columns={[
            { label: "業種" },
            { label: "スコア", right: true },
            { label: "シグナル", right: true },
          ]}
        >
          {top.map((row) => (
            <tr key={row.code} className="hover:[&>td]:bg-surface-2">
              <Td>
                <span className="text-ink">{row.label}</span>{" "}
                <span className="num text-[11px] text-ink-subtle">{row.code}</span>
              </Td>
              <Td right>
                <span className="inline-flex items-center justify-end gap-2">
                  <span className="h-1 w-12 overflow-hidden rounded-full bg-hairline">
                    <i
                      className="block h-full bg-accent"
                      style={{ width: `${Math.max(0, Math.min(1, row.score)) * 100}%` }}
                    />
                  </span>
                  <span className="num">{row.score.toFixed(2)}</span>
                </span>
              </Td>
              <Td right>
                <span className={`num ${row.signal >= 0 ? "text-up" : "text-down"}`}>
                  {row.signal >= 0 ? "+" : ""}
                  {row.signal.toFixed(2)}
                </span>
              </Td>
            </tr>
          ))}
        </DataTable>
      </StatusBlock>
    </Card>
  );
}
