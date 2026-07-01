"use client";

// 米国株 詳細（screens.md #3 ミラー・ADR-039(B)/ADR-055）。提示専用（保有登録・ドシエは持たない）。
// 日本株 /stocks/[code] のミラー。チャート（ローソク足）が本体。マスタ＋valuation snapshot を出す。
// データ取得はブラウザ fetch（ADR-005）。USD（ドル）表示。未焼成（valuation=null）でも 200 で描く。
// 日本株固有の DossierSection（watchlist 巡回・調査パイプライン）は米株に無いので載せない（市場分離・ADR-031）。

import { CandleChart } from "@/components/chart/CandleChart";
import { StatusBlock } from "@/components/ui/StatusBlock";
import { gicsSectorLabel } from "@/components/us-screener/UsScreenerFilters";
import { getUsQuotes, getUsStock } from "@/lib/api";
import { fmtMarketCapUsd, fmtRatio, fmtUsd, pct } from "@/lib/format";
import { useApi } from "@/lib/use-api";
import Link from "next/link";
import { useParams } from "next/navigation";

// バリュエーションの 1 指標セル（事実の羅列・AI に計算させない＝ADR-014）。
function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[11px] text-ink-subtle">{label}</span>
      <span className="num font-semibold text-[14px]">{value}</span>
    </div>
  );
}

export default function UsStockDetailPage() {
  const params = useParams<{ symbol: string }>();
  const symbol = params.symbol;
  const { data, error, loading } = useApi(
    (signal) =>
      Promise.all([getUsStock(symbol, signal), getUsQuotes(symbol, undefined, undefined, signal)]),
    [symbol],
  );
  const detail = data?.[0] ?? null;
  const quotes = data?.[1] ?? null;
  const v = detail?.valuation ?? null;

  return (
    <>
      <div className="mb-3 flex items-baseline gap-3">
        <Link href="/us-stocks" className="text-[12px] text-accent">
          ← US Screener
        </Link>
        <div className="num font-semibold text-[20px] tracking-[-0.4px]">{symbol}</div>
        <div className="text-[13px] text-ink-muted">{detail?.company_name ?? ""}</div>
        {detail?.gics_sector && (
          <div className="text-[12px] text-ink-subtle">{gicsSectorLabel(detail.gics_sector)}</div>
        )}
      </div>

      {/* バリュエーション事実（夜間 calc_us_valuation が焼いた事実）。取得失敗を「未焼成（—）」に
          化けさせないため StatusBlock で loading/error を出す。成功時の null は「未焼成」を明示（#30）。 */}
      <section className="mb-3 rounded-lg border border-hairline bg-surface-1">
        <div className="flex items-center justify-between border-hairline border-b px-3 py-2">
          <h2 className="font-semibold text-[14px] tracking-[-0.1px]">バリュエーション（USD）</h2>
          <span className="text-[11px] text-ink-subtle">
            {loading
              ? "取得中…"
              : error
                ? "取得失敗"
                : v?.as_of_date
                  ? `${v.as_of_date} 基準`
                  : "未焼成（夜間バッチ待ち）"}
          </span>
        </div>
        <div className="p-3">
          <StatusBlock
            loading={loading}
            error={error}
            errorHint={<>backend 起動と、夜間バッチ（calc_us_valuation）の実行を確認するのだ。</>}
          >
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-6">
              <Metric label="終値" value={fmtUsd(v?.close)} />
              <Metric label="PER" value={fmtRatio(v?.per)} />
              <Metric label="PBR" value={fmtRatio(v?.pbr)} />
              <Metric label="配当利回り" value={pct(v?.dividend_yield)} />
              <Metric label="時価総額" value={fmtMarketCapUsd(v?.market_cap)} />
              <Metric label="ROE" value={pct(v?.roe)} />
              <Metric label="営業利益率" value={pct(v?.operating_margin)} />
              <Metric label="純利益率" value={pct(v?.net_margin)} />
              <Metric label="売上成長(YoY)" value={pct(v?.revenue_growth_yoy)} />
              <Metric label="純益成長(YoY)" value={pct(v?.profit_growth_yoy)} />
              <Metric label="EPS成長(YoY)" value={pct(v?.eps_growth_yoy)} />
              <Metric label="時価総額順位" value={v?.market_cap_rank?.toString() ?? "—"} />
            </div>
          </StatusBlock>
        </div>
      </section>

      <section className="rounded-lg border border-hairline bg-surface-1">
        <div className="flex items-center justify-between border-hairline border-b px-3 py-2">
          <h2 className="font-semibold text-[14px] tracking-[-0.1px]">株価（日足）</h2>
          <span className="text-[11px] text-ink-subtle">yfinance・USD</span>
        </div>
        <div className="p-3">
          <StatusBlock
            loading={loading}
            error={error}
            empty={quotes?.length === 0}
            errorHint={<>backend 起動と、夜間バッチ（fetch_us_quotes）の実行を確認するのだ。</>}
            emptyText="この銘柄の日足がまだないのだ。夜間バッチ（fetch_us_quotes）を実行するのだ。"
          >
            {quotes && quotes.length > 0 && <CandleChart quotes={quotes} />}
          </StatusBlock>
        </div>
      </section>
    </>
  );
}
