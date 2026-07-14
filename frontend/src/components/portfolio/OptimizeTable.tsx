// 最適化結果テーブル（screens.md #5・phase2-spec.md §6）。
// current → target（×100 %）と delta（増=text-up / 減=text-down・符号付き）を表示。
// infeasible=true のときは「制約が厳しく解なし」と constraints_applied を提示。
// 比率は 0..1 で来るので UI でのみ ×100 して %（ADR-008 / phase2-spec.md §0）。
// 鮮度注記のプラン名・遅延幅は焼き込まず、親（page）が /health から取った jquants を props で受け
// lib/jquants の freshnessNote で組む（ADR-061・feature は自分で GET しない＝frontend-component-pattern）。

import { DataTable, Td } from "@/components/ui/DataTable";
import type { JquantsStatus, OptimizeResult } from "@/lib/api";
import { deltaPct, pct } from "@/lib/format";
import { freshnessNote } from "@/lib/jquants";

type Props = {
  result: OptimizeResult;
  jquants: JquantsStatus | undefined; // /health のプラン状態（未取得なら undefined）
};

export function OptimizeTable({ result, jquants }: Props) {
  // 解なし（infeasible）のときは制約内容を表示して緩め方を案内するのだ。
  if (result.infeasible) {
    const ca = result.constraints_applied;
    return (
      <div className="rounded-md border border-warning bg-canvas px-3 py-3">
        <div className="font-semibold text-[13px] text-warning">
          制約が厳しく解なし（上限を緩めるか銘柄を増やすのだ）
        </div>
        <div className="mt-2 text-[12px] text-ink-muted">適用された制約:</div>
        <ul className="mt-1 space-y-0.5 text-[12px] text-ink-muted">
          <li>
            現金目標比率:{" "}
            <span className="num text-ink">
              {ca.target_cash_ratio != null ? pct(ca.target_cash_ratio) : "—"}
            </span>
          </li>
          <li>
            1 銘柄上限:{" "}
            <span className="num text-ink">
              {ca.max_position_weight != null ? pct(ca.max_position_weight) : "—"}
            </span>
          </li>
          {ca.sector_caps && Object.keys(ca.sector_caps).length > 0 && (
            <li>
              業種上限:{" "}
              <span className="num text-ink">
                {Object.entries(ca.sector_caps)
                  .map(([k, v]) => `${k}: ${pct(v)}`)
                  .join(" / ")}
              </span>
            </li>
          )}
        </ul>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {/* サマリカード */}
      <div className="grid grid-cols-3 gap-2">
        {[
          { label: "期待年率リターン", value: pct(result.expected_annual_return) },
          { label: "期待ボラティリティ", value: pct(result.expected_annual_volatility) },
          {
            label: "期待シャープ比",
            value: result.expected_sharpe != null ? result.expected_sharpe.toFixed(2) : "—",
          },
        ].map((c) => (
          <div key={c.label} className="rounded-md border border-hairline bg-canvas px-2.5 py-2">
            <div className="text-[11px] text-ink-muted">{c.label}</div>
            <div className="num mt-0.5 font-semibold text-[15px] tracking-[-0.2px]">{c.value}</div>
          </div>
        ))}
      </div>

      {/* 遅延・鮮度注記 */}
      {result.is_delayed && result.as_of && (
        <div className="text-[11px] text-ink-subtle">
          {freshnessNote(jquants, result.as_of, true)}（評価額は遅延値）
        </div>
      )}

      {/* 最適化テーブル（現状 → 目標・差分）*/}
      <DataTable
        columns={[
          { label: "コード / 銘柄" },
          { label: "現状", right: true },
          { label: "目標", right: true },
          { label: "差分", right: true },
        ]}
      >
        {/* 銘柄行 */}
        {result.weights.map((w) => (
          <tr key={w.code} className="hover:[&>td]:bg-surface-2">
            <Td>
              <span className="num font-semibold text-accent">{w.code}</span>{" "}
              <span className="text-[12px] text-ink-muted">{w.company_name ?? "—"}</span>
            </Td>
            <Td right className="num text-ink-muted">
              {pct(w.current_weight)}
            </Td>
            <Td right className="num font-semibold">
              {pct(w.target_weight)}
            </Td>
            <Td
              right
              className={`num font-semibold ${
                w.delta > 0 ? "text-up" : w.delta < 0 ? "text-down" : "text-ink-subtle"
              }`}
            >
              {deltaPct(w.delta)}
            </Td>
          </tr>
        ))}
        {/* 現金行 */}
        <tr className="hover:[&>td]:bg-surface-2">
          <Td>
            <span className="text-ink-muted">現金</span>
          </Td>
          <Td right className="num text-ink-muted">
            —
          </Td>
          <Td right className="num font-semibold">
            {pct(result.cash_weight)}
          </Td>
          <Td right className="text-ink-subtle">
            —
          </Td>
        </tr>
      </DataTable>

      <div className="text-[11px] text-ink-subtle">
        目標: {result.objective} ／ 現金目標:{" "}
        {result.constraints_applied.target_cash_ratio != null
          ? pct(result.constraints_applied.target_cash_ratio)
          : "—"}{" "}
        ／ 1 銘柄上限:{" "}
        {result.constraints_applied.max_position_weight != null
          ? pct(result.constraints_applied.max_position_weight)
          : "—"}
      </div>
    </div>
  );
}
