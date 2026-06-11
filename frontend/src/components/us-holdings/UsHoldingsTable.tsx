// 米株保有一覧（Phase 7(B-2)・ADR-055）。投信 FundHoldingsTable のミラー。
// symbol・社名・株数・平均取得単価(USD)・最終終値(USD)・評価額(JPY)・含み損益(JPY)・比率を表示。
// 評価系は FX 未取得 or close 未取得のとき "—" で欠損表示（捏造しない＝ADR-014）。
// holdings は props で受ける（取得・mutation は UsSection が持つ＝frontend-component-pattern (c)）。

import { DataTable, Td } from "@/components/ui/DataTable";
import type { UsHolding } from "@/lib/api";
import { fmtJpy, fmtUsd, pct } from "@/lib/format";

type Props = { holdings: UsHolding[] };

export function UsHoldingsTable({ holdings }: Props) {
  return (
    <DataTable
      columns={[
        { label: "銘柄" },
        { label: "株数", right: true },
        { label: "平均取得(USD)", right: true },
        { label: "最終終値(USD)", right: true },
        { label: "評価額(JPY)", right: true },
        { label: "含み損益(JPY)", right: true },
        { label: "比率", right: true },
      ]}
    >
      {holdings.map((h) => {
        const pnlPos = h.unrealized_pnl_jpy != null && h.unrealized_pnl_jpy >= 0;
        return (
          <tr key={h.id} className="hover:[&>td]:bg-surface-2">
            <Td>
              <span className="num font-semibold text-accent">{h.symbol}</span>{" "}
              <span className="text-[12px] text-ink-muted">{h.company_name ?? "—"}</span>
              {h.gics_sector && (
                <span className="num block text-[11px] text-ink-subtle">{h.gics_sector}</span>
              )}
            </Td>
            <Td right className="num">
              {h.shares.toLocaleString("en-US")}
            </Td>
            <Td right className="num text-ink-muted">
              {fmtUsd(h.avg_cost)}
            </Td>
            <Td right className="num">
              {h.last_close != null ? (
                <>
                  {fmtUsd(h.last_close)}
                  {h.close_date && (
                    <span className="num block text-[11px] text-ink-subtle">{h.close_date}</span>
                  )}
                </>
              ) : (
                "—"
              )}
            </Td>
            <Td right className="num font-semibold">
              {fmtJpy(h.market_value_jpy)}
              {h.fx_rate != null && (
                <span className="num block text-[11px] text-ink-subtle">
                  ¥{h.fx_rate.toFixed(2)}/USD
                </span>
              )}
            </Td>
            <Td
              right
              className={`num font-semibold ${
                h.unrealized_pnl_jpy == null ? "text-ink-subtle" : pnlPos ? "text-up" : "text-down"
              }`}
            >
              {h.unrealized_pnl_jpy != null
                ? `${pnlPos ? "+" : ""}${fmtJpy(h.unrealized_pnl_jpy)}`
                : "—"}
            </Td>
            <Td right className="num">
              {pct(h.weight)}
            </Td>
          </tr>
        );
      })}
    </DataTable>
  );
}
