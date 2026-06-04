"use client";

import { CandleChart } from "@/components/chart/CandleChart";
import { getQuotes, getStock } from "@/lib/api";
import { useApi } from "@/lib/use-api";
import Link from "next/link";
import { useParams } from "next/navigation";

// 銘柄詳細（screens.md #3 のハブ最小版・Phase 0）。チャートが Phase 0 完了条件の本体。
// 財務・ドシエ・watchlist 追加などのセクションは後続 Phase で足す。
export default function StockDetailPage() {
  const params = useParams<{ code: string }>();
  const code = params.code;
  const { data, error } = useApi(
    (signal) =>
      Promise.all([getStock(code, signal), getQuotes(code, undefined, undefined, signal)]),
    [code],
  );
  const stock = data?.[0] ?? null;
  const quotes = data?.[1] ?? null;

  return (
    <>
      <div className="mb-3 flex items-baseline gap-3">
        <Link href="/stocks" className="text-[12px] text-accent">
          ← Stocks
        </Link>
        <div className="num font-semibold text-[20px] tracking-[-0.4px]">{code}</div>
        <div className="text-[13px] text-ink-muted">{stock?.company_name ?? ""}</div>
      </div>

      <section className="rounded-lg border border-hairline bg-surface-1">
        <div className="flex items-center justify-between border-hairline border-b px-3 py-2">
          <h2 className="font-semibold text-[14px] tracking-[-0.1px]">株価（日足）</h2>
          <span className="text-[11px] text-ink-subtle">
            J-Quants Free・約3か月前まで（12週遅延）
          </span>
        </div>
        <div className="p-3">
          {error && <div className="py-8 text-center text-[13px] text-down">⚠ {error}</div>}
          {!error && quotes === null && (
            <div className="py-8 text-center text-[13px] text-ink-subtle">読み込み中…</div>
          )}
          {!error && quotes?.length === 0 && (
            <div className="py-8 text-center text-[13px] text-ink-subtle">
              この銘柄の日足がまだないのだ。バックフィルを実行するのだ。
            </div>
          )}
          {!error && quotes && quotes.length > 0 && <CandleChart quotes={quotes} />}
        </div>
      </section>
    </>
  );
}
