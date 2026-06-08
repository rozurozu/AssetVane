// 投信 保有一覧（ADR-054）。株の保有テーブル（portfolio/page.tsx）をミラー。
// 銘柄名・口数・現在 NAV(基準日)・評価額・含み損益（損益で色分け）・構成比＋ NAV 推移スパークライン。
// 評価額・含み損益は backend 計算（market_value/unrealized_pnl）をそのまま表示する（再計算しない＝ADR-014）。
// holdings は props で受ける（取得はページ・mutation で書き換わるので useState 保持＝feature 規約 (c)）。

import { FundNavSparkline } from "@/components/fund/FundNavSparkline";
import { DataTable, Td } from "@/components/ui/DataTable";
import type { FundHolding } from "@/lib/api";
import { fmtJpy, pct } from "@/lib/format";

export function FundHoldingsTable({ holdings }: { holdings: FundHolding[] }) {
  return (
    <DataTable
      columns={[
        { label: "投信" },
        { label: "口数", right: true },
        { label: "現在 NAV", right: true },
        { label: "NAV 推移" },
        { label: "評価額", right: true },
        { label: "含み損益", right: true },
        { label: "比率", right: true },
      ]}
    >
      {holdings.map((h) => {
        const pnlPos = h.unrealized_pnl != null && h.unrealized_pnl >= 0;
        return (
          <tr key={h.isin} className="hover:[&>td]:bg-surface-2">
            <Td>
              <span className="text-[13px] font-semibold">{h.name ?? h.isin}</span>
              <span className="num block text-[11px] text-ink-subtle">{h.isin}</span>
            </Td>
            <Td right className="num">
              {h.units.toLocaleString("ja-JP")}
            </Td>
            <Td right className="num">
              {fmtJpy(h.last_nav)}
              {h.nav_date && (
                <span className="num block text-[11px] text-ink-subtle">{h.nav_date}</span>
              )}
            </Td>
            <Td>
              <FundNavSparkline isin={h.isin} />
            </Td>
            <Td right className="num font-semibold">
              {fmtJpy(h.market_value)}
            </Td>
            <Td
              right
              className={`num font-semibold ${
                h.unrealized_pnl == null ? "text-ink-subtle" : pnlPos ? "text-up" : "text-down"
              }`}
            >
              {h.unrealized_pnl != null ? `${pnlPos ? "+" : ""}${fmtJpy(h.unrealized_pnl)}` : "—"}
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
