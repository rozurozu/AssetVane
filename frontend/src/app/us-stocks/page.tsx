"use client";

// 米国株スクリーナー（screens.md #2 ミラー・ADR-039(B)/ADR-055）。提示専用（保有登録・FX 換算は B-2 送り）。
// 日本株 /stocks のミラー。バリュエーション（PER/PBR/時価総額/配当利回り/ROE/利益率/成長率）で
// 米株を絞り込む。データ取得はブラウザ fetch（ADR-005）。絞り込み・ランクは backend が読み取り時に
// 計算（ADR-026/055）。値は夜間バッチの最新営業日ベース（calc_us_valuation）。USD（ドル）表示。
// 日本株固有の watchlist 星・保存フィルタ（/screening-filters は JP 専用）は持たない（市場分離・ADR-031）。

import { DataTable, Td } from "@/components/ui/DataTable";
import { StatusBlock } from "@/components/ui/StatusBlock";
import { UsScreenerFilters, gicsSectorLabel } from "@/components/us-screener/UsScreenerFilters";
import { type UsScreenCriteria, screenUsStocks } from "@/lib/api";
import { fmtMarketCapUsd, fmtRatio, pct } from "@/lib/format";
import { useApi } from "@/lib/use-api";
import Link from "next/link";
import { useState } from "react";

const DEFAULT_CRITERIA: UsScreenCriteria = { sort_by: "market_cap", sort_dir: "desc", limit: 200 };

export default function UsStocksPage() {
  // draft = 編集中の条件、applied = 実際に問い合わせ中の条件（「絞り込む」で同期）。
  const [draft, setDraft] = useState<UsScreenCriteria>(DEFAULT_CRITERIA);
  const [applied, setApplied] = useState<UsScreenCriteria>(DEFAULT_CRITERIA);
  const appliedKey = JSON.stringify(applied);

  const { data: rows, error, loading } = useApi((s) => screenUsStocks(applied, s), [appliedKey]);

  return (
    <>
      <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
        <div>
          <div className="font-semibold text-[20px] tracking-[-0.4px]">US Screener</div>
          <div className="mt-0.5 text-[12px] text-ink-muted">
            米国株をバリュエーションで絞り込む（前夜終値ベース・USD・yfinance）
          </div>
        </div>
      </div>

      <div className="mb-3">
        <UsScreenerFilters
          draft={draft}
          onChange={setDraft}
          onApply={() => setApplied(draft)}
          onReset={() => {
            setDraft(DEFAULT_CRITERIA);
            setApplied(DEFAULT_CRITERIA);
          }}
        />
      </div>

      <section className="rounded-lg border border-hairline bg-surface-1">
        <StatusBlock
          loading={loading}
          error={error}
          empty={rows?.length === 0}
          className="p-4"
          errorHint={<>backend 起動と、夜間バッチ（calc_us_valuation）の実行を確認するのだ。</>}
          emptyText="条件に合う銘柄がないのだ。条件を緩めるか、夜間バッチでデータを焼くのだ。"
        >
          {rows && rows.length > 0 && (
            <>
              <DataTable
                columns={[
                  { label: "シンボル" },
                  { label: "銘柄名" },
                  { label: "業種" },
                  { label: "PER", right: true },
                  { label: "PBR", right: true },
                  { label: "配当利回り", right: true },
                  { label: "ROE", right: true },
                  { label: "時価総額", right: true },
                  { label: "時価総額順位", right: true },
                ]}
              >
                {rows.map((r) => (
                  <tr key={r.symbol} className="hover:[&>td]:bg-surface-2">
                    <Td>
                      <Link
                        href={`/us-stocks/${r.symbol}`}
                        className="num font-semibold text-accent"
                      >
                        {r.symbol}
                      </Link>
                    </Td>
                    <Td>
                      <Link href={`/us-stocks/${r.symbol}`} className="hover:text-accent">
                        {r.company_name ?? "—"}
                      </Link>
                    </Td>
                    <Td className="text-ink-muted">{gicsSectorLabel(r.gics_sector)}</Td>
                    <Td right className="num">
                      {fmtRatio(r.per)}
                    </Td>
                    <Td right className="num">
                      {fmtRatio(r.pbr)}
                    </Td>
                    <Td right className="num">
                      {pct(r.dividend_yield)}
                    </Td>
                    <Td right className="num">
                      {pct(r.roe)}
                    </Td>
                    <Td right className="num">
                      {fmtMarketCapUsd(r.market_cap)}
                    </Td>
                    <Td right className="num text-ink-muted">
                      {r.market_cap_rank ?? "—"}
                    </Td>
                  </tr>
                ))}
              </DataTable>
              <div className="flex items-center justify-between border-hairline-soft border-t px-3 py-2 text-[11px] text-ink-subtle">
                <span>
                  {rows.length} 件（最大 {applied.limit ?? 200} 件）
                </span>
              </div>
            </>
          )}
        </StatusBlock>
      </section>
    </>
  );
}
