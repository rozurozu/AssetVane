"use client";

import { DataTable, Td } from "@/components/ui/DataTable";
import { StatusBlock } from "@/components/ui/StatusBlock";
import { type SignalType, getSignals } from "@/lib/api";
import { useApi } from "@/lib/use-api";
import Link from "next/link";
import { useState } from "react";

// シグナル一覧「今日の強い銘柄」（screens.md #4・phase1-spec.md §6.2・Phase 1 Trend Vane）。
// 夜間バッチが事前計算した signals を /signals 経由で読むだけ（AI に計算させない＝ADR-014）。
// 行クリックで銘柄詳細へ。スタイルは Stocks 一覧・Dashboard signals と同じ DESIGN.md トークン。

// type 切替タブ（全 / momentum / volume_spike）。値 undefined は全 type。
const TYPE_TABS: { key: string; label: string; value: SignalType | undefined }[] = [
  { key: "all", label: "全", value: undefined },
  { key: "momentum", label: "momentum", value: "momentum" },
  { key: "volume_spike", label: "volume_spike", value: "volume_spike" },
];

export default function SignalsPage() {
  // 選択中の type フィルタ（undefined = 全 type）。
  const [type, setType] = useState<SignalType | undefined>(undefined);

  const { data, error, loading } = useApi(
    (signal) => getSignals(type ? { type } : undefined, signal),
    [type],
  );

  return (
    <>
      <div className="mb-3">
        <div className="font-semibold text-[20px] tracking-[-0.4px]">Signals（Trend Vane）</div>
        <div className="mt-0.5 text-[12px] text-ink-muted">
          {data
            ? data.is_delayed
              ? `今日の強い銘柄。J-Quants Free・12週遅延・${data.date}基準`
              : `今日の強い銘柄。${data.date} 算出`
            : "夜間バッチが算出した「今日の強い銘柄」。行をクリックで株価チャートへ"}
        </div>
      </div>

      {/* type 切替タブ。アクティブは surface-2 へ lift（青の面塗りはしない＝DESIGN.md） */}
      <div className="mb-3 flex gap-1">
        {TYPE_TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => setType(t.value)}
            className={`rounded-md px-2.5 py-1 text-[12px] ${
              type === t.value
                ? "bg-surface-2 font-semibold text-ink"
                : "text-ink-muted hover:bg-surface-2 hover:text-ink"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      <section className="rounded-lg border border-hairline bg-surface-1">
        <StatusBlock
          loading={loading}
          error={error}
          empty={data?.signals.length === 0}
          className="p-4"
          errorHint="backend 起動と、夜間バッチ（POST /batch/run）の実行を確認するのだ。"
          emptyText="まだシグナルがないのだ。`POST /batch/run` で夜間バッチを回すのだ。"
        >
          {data && (
            <DataTable
              columns={[
                { label: "コード / 銘柄" },
                { label: "スコア", right: true },
                { label: "5日", right: true },
                { label: "シグナル" },
              ]}
            >
              {data.signals.map((s) => {
                const d5 = s.payload.change_5d;
                return (
                  <tr key={`${s.code}-${s.signal_type}`} className="hover:[&>td]:bg-surface-2">
                    <Td>
                      <Link href={`/stocks/${s.code}`} className="hover:text-accent">
                        <span className="num font-semibold text-accent">{s.code}</span>{" "}
                        <span className="text-[12px] text-ink-muted">{s.company_name ?? "—"}</span>
                      </Link>
                    </Td>
                    <Td right>
                      <span className="inline-flex items-center justify-end gap-2">
                        <span className="h-1 w-12 overflow-hidden rounded-full bg-hairline">
                          <i
                            className="block h-full bg-accent"
                            style={{ width: `${s.score * 100}%` }}
                          />
                        </span>
                        <span className="num">{s.score.toFixed(2)}</span>
                      </span>
                    </Td>
                    <Td right>
                      {d5 != null ? (
                        <span className={`num ${d5 >= 0 ? "text-up" : "text-down"}`}>
                          {d5 >= 0 ? "+" : ""}
                          {(d5 * 100).toFixed(1)}%
                        </span>
                      ) : (
                        <span className="text-ink-subtle">—</span>
                      )}
                    </Td>
                    <Td>
                      <span className="rounded-sm bg-surface-2 px-1.5 py-0.5 text-[12px] text-ink-muted">
                        {s.payload.label ?? "—"}
                      </span>
                    </Td>
                  </tr>
                );
              })}
            </DataTable>
          )}
        </StatusBlock>
      </section>
    </>
  );
}
