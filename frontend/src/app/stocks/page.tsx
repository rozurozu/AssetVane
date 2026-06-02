"use client";

import { type Stock, getStocks } from "@/lib/api";
import Link from "next/link";
import { useEffect, useState } from "react";

// 銘柄一覧（screens.md #2・Phase 0）。FastAPI /stocks を叩いて表示し、各行から銘柄詳細へ。
// データ取得はブラウザ fetch（AdvisorChat と同じ流儀）。
export default function StocksPage() {
  const [stocks, setStocks] = useState<Stock[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getStocks()
      .then(setStocks)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  return (
    <>
      <div className="mb-3">
        <div className="font-semibold text-[20px] tracking-[-0.4px]">Stocks</div>
        <div className="mt-0.5 text-[12px] text-ink-muted">
          取得済みの銘柄。行をクリックで株価チャートへ（J-Quants Free・12週遅延）
        </div>
      </div>

      <section className="rounded-lg border border-hairline bg-surface-1">
        {error && (
          <div className="p-4 text-[13px] text-down">
            ⚠ 取得に失敗: {error}
            <div className="mt-1 text-[12px] text-ink-subtle">
              backend 起動と、バックフィル（uv run python -m
              app.scripts.backfill）の実行を確認するのだ。
            </div>
          </div>
        )}
        {!error && stocks === null && (
          <div className="p-4 text-[13px] text-ink-subtle">読み込み中…</div>
        )}
        {!error && stocks?.length === 0 && (
          <div className="p-4 text-[13px] text-ink-subtle">
            まだ銘柄がないのだ。`uv run python -m app.scripts.backfill` でデータを入れるのだ。
          </div>
        )}
        {!error && stocks && stocks.length > 0 && (
          <table className="w-full border-collapse">
            <thead>
              <tr>
                {["コード", "銘柄名", "33業種", "市場"].map((h) => (
                  <th
                    key={h}
                    className="h-8 border-hairline border-b px-2.5 text-left font-medium text-[11px] text-ink-muted uppercase tracking-[0.3px]"
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {stocks.map((s) => (
                <tr key={s.code} className="hover:[&>td]:bg-surface-2">
                  <td className="h-[34px] border-hairline-soft border-b px-2.5 text-[13px]">
                    <Link href={`/stocks/${s.code}`} className="num font-semibold text-accent">
                      {s.code}
                    </Link>
                  </td>
                  <td className="h-[34px] border-hairline-soft border-b px-2.5 text-[13px]">
                    <Link href={`/stocks/${s.code}`} className="hover:text-accent">
                      {s.company_name ?? "—"}
                    </Link>
                  </td>
                  <td className="num h-[34px] border-hairline-soft border-b px-2.5 text-[13px] text-ink-muted">
                    {s.sector33_code ?? "—"}
                  </td>
                  <td className="num h-[34px] border-hairline-soft border-b px-2.5 text-[13px] text-ink-muted">
                    {s.market_code ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </>
  );
}
