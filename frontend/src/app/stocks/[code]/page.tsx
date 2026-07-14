"use client";

import { CandleChart } from "@/components/chart/CandleChart";
import { DossierSection } from "@/components/dossier/DossierSection";
import { StatusBlock } from "@/components/ui/StatusBlock";
import { getQuotes, getStock } from "@/lib/api";
import { jquantsSourceNote, useJquantsStatus } from "@/lib/jquants";
import { useApi } from "@/lib/use-api";
import Link from "next/link";
import { useParams } from "next/navigation";

// 銘柄詳細（screens.md #3 のハブ最小版・Phase 0）。チャートが Phase 0 完了条件の本体。
// 財務・ドシエ・watchlist 追加などのセクションは後続 Phase で足す。
export default function StockDetailPage() {
  const params = useParams<{ code: string }>();
  const code = params.code;
  const { data, error, loading } = useApi(
    (signal) =>
      Promise.all([getStock(code, signal), getQuotes(code, undefined, undefined, signal)]),
    [code],
  );
  const stock = data?.[0] ?? null;
  const quotes = data?.[1] ?? null;
  // 日足の取得元注記（旧・ハードコードの "J-Quants Free・約3か月前まで（12週遅延）" を廃止し
  // /health の実プラン由来に統一＝ADR-061。プランを変えても嘘が残らない）。
  const jquants = useJquantsStatus();

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
          <span className="text-[11px] text-ink-subtle">{jquantsSourceNote(jquants)}</span>
        </div>
        <div className="p-3">
          <StatusBlock
            loading={loading}
            error={error}
            empty={quotes?.length === 0}
            errorHint={<>backend 起動と、バックフィル（日足取得）の実行を確認するのだ。</>}
            emptyText="この銘柄の日足がまだないのだ。バックフィルを実行するのだ。"
          >
            {quotes && quotes.length > 0 && <CandleChart quotes={quotes} />}
          </StatusBlock>
        </div>
      </section>

      {/* ドシエ（定性調査・Phase 4）。チャートの下に挿す（screens.md #3・銘柄詳細内のセクション）。 */}
      <div className="mt-3">
        <DossierSection code={code} />
      </div>
    </>
  );
}
