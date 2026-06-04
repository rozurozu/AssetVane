"use client";

import { DataTable, Td } from "@/components/ui/DataTable";
import { StatusBlock } from "@/components/ui/StatusBlock";
import { getStocks } from "@/lib/api";
import { useApi } from "@/lib/use-api";
import Link from "next/link";

// 銘柄一覧（screens.md #2・Phase 0）。FastAPI /stocks を叩いて表示し、各行から銘柄詳細へ。
// データ取得はブラウザ fetch（AdvisorChat と同じ流儀）。
export default function StocksPage() {
  const { data: stocks, error, loading } = useApi((signal) => getStocks(undefined, signal), []);

  return (
    <>
      <div className="mb-3">
        <div className="font-semibold text-[20px] tracking-[-0.4px]">Stocks</div>
        <div className="mt-0.5 text-[12px] text-ink-muted">
          取得済みの銘柄。行をクリックで株価チャートへ（J-Quants Free・12週遅延）
        </div>
      </div>

      <section className="rounded-lg border border-hairline bg-surface-1">
        <StatusBlock
          loading={loading}
          error={error}
          empty={stocks?.length === 0}
          className="p-4"
          errorHint={
            <>
              backend 起動と、バックフィル（uv run python -m
              app.scripts.backfill）の実行を確認するのだ。
            </>
          }
          emptyText="まだ銘柄がないのだ。`uv run python -m app.scripts.backfill` でデータを入れるのだ。"
        >
          {stocks && stocks.length > 0 && (
            <DataTable
              columns={[
                { label: "コード" },
                { label: "銘柄名" },
                { label: "33業種" },
                { label: "市場" },
              ]}
            >
              {stocks.map((s) => (
                <tr key={s.code} className="hover:[&>td]:bg-surface-2">
                  <Td>
                    <Link href={`/stocks/${s.code}`} className="num font-semibold text-accent">
                      {s.code}
                    </Link>
                  </Td>
                  <Td>
                    <Link href={`/stocks/${s.code}`} className="hover:text-accent">
                      {s.company_name ?? "—"}
                    </Link>
                  </Td>
                  <Td className="num text-ink-muted">{s.sector33_code ?? "—"}</Td>
                  <Td className="num text-ink-muted">{s.market_code ?? "—"}</Td>
                </tr>
              ))}
            </DataTable>
          )}
        </StatusBlock>
      </section>
    </>
  );
}
