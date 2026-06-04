"use client";

// 株式スクリーナー（screens.md #2・ADR-031）。日本株専用（米株は Phase 7 で /us-stocks 別ルート）。
// バリュエーション（PER/PBR/時価総額/配当利回り）で全銘柄を絞り込み、条件を保存できる。
// データ取得はブラウザ fetch（ADR-005）。絞り込み・ランクは backend が読み取り時に計算（ADR-026）。
// 値は夜間バッチの最新営業日ベース（calc_valuation）。テクニカル軸の複合は TODO（必須・後続）。

import { SavedFilterBar } from "@/components/screener/SavedFilterBar";
import { ScreenerFilters } from "@/components/screener/ScreenerFilters";
import { DataTable, Td } from "@/components/ui/DataTable";
import { StatusBlock } from "@/components/ui/StatusBlock";
import { type SavedFilter, type ScreenCriteria, getFilters, screenStocks } from "@/lib/api";
import { fmtMarketCap, fmtRatio, pct } from "@/lib/format";
import { useApi } from "@/lib/use-api";
import Link from "next/link";
import { useEffect, useState } from "react";

const DEFAULT_CRITERIA: ScreenCriteria = { sort_by: "market_cap", sort_dir: "desc", limit: 200 };

export default function StocksPage() {
  // draft = 編集中の条件、applied = 実際に問い合わせ中の条件（「絞り込む」で同期）。
  const [draft, setDraft] = useState<ScreenCriteria>(DEFAULT_CRITERIA);
  const [applied, setApplied] = useState<ScreenCriteria>(DEFAULT_CRITERIA);
  const appliedKey = JSON.stringify(applied);

  const { data: rows, error, loading } = useApi((s) => screenStocks(applied, s), [appliedKey]);

  // 保存フィルタは画面操作で書き換わる（作成/削除）ので useState 管理・初回のみ取得（規約 c）。
  const [filters, setFilters] = useState<SavedFilter[]>([]);
  useEffect(() => {
    let alive = true;
    getFilters()
      .then((f) => alive && setFilters(f))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  function onSavedFilter(f: SavedFilter) {
    setFilters((prev) => {
      const i = prev.findIndex((x) => x.id === f.id);
      if (i === -1) return [f, ...prev];
      const next = [...prev];
      next[i] = f;
      return next;
    });
  }

  function loadCriteria(c: ScreenCriteria) {
    setDraft(c);
    setApplied(c); // 読み込んだら即適用
  }

  return (
    <>
      <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
        <div>
          <div className="font-semibold text-[20px] tracking-[-0.4px]">Screener</div>
          <div className="mt-0.5 text-[12px] text-ink-muted">
            日本株をバリュエーションで絞り込む（前夜終値ベース・J-Quants）
          </div>
        </div>
        <SavedFilterBar
          filters={filters}
          appliedCriteria={applied}
          onLoad={loadCriteria}
          onSaved={onSavedFilter}
          onDeleted={(id) => setFilters((prev) => prev.filter((x) => x.id !== id))}
        />
      </div>

      <div className="mb-3">
        <ScreenerFilters
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
          errorHint={<>backend 起動と、夜間バッチ（calc_valuation）の実行を確認するのだ。</>}
          emptyText="条件に合う銘柄がないのだ。条件を緩めるか、夜間バッチでデータを焼くのだ。"
        >
          {rows && rows.length > 0 && (
            <>
              <DataTable
                columns={[
                  { label: "コード" },
                  { label: "銘柄名" },
                  { label: "33業種" },
                  { label: "PER", right: true },
                  { label: "PBR", right: true },
                  { label: "配当利回り", right: true },
                  { label: "時価総額", right: true },
                  { label: "時価総額順位", right: true },
                ]}
              >
                {rows.map((r) => (
                  <tr key={r.code} className="hover:[&>td]:bg-surface-2">
                    <Td>
                      <Link href={`/stocks/${r.code}`} className="num font-semibold text-accent">
                        {r.code}
                      </Link>
                    </Td>
                    <Td>
                      <Link href={`/stocks/${r.code}`} className="hover:text-accent">
                        {r.company_name ?? "—"}
                      </Link>
                    </Td>
                    <Td className="num text-ink-muted">{r.sector33_code ?? "—"}</Td>
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
                      {fmtMarketCap(r.market_cap)}
                    </Td>
                    <Td right className="num text-ink-muted">
                      {r.market_cap_rank ?? "—"}
                    </Td>
                  </tr>
                ))}
              </DataTable>
              <div className="border-hairline-soft border-t px-3 py-2 text-[11px] text-ink-subtle">
                {rows.length} 件（最大 {applied.limit ?? 200} 件）
              </div>
            </>
          )}
        </StatusBlock>
      </section>
    </>
  );
}
