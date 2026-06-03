"use client";

import { type SignalType, type SignalsResponse, getSignals } from "@/lib/api";
import Link from "next/link";
import { useEffect, useState } from "react";

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
  const [data, setData] = useState<SignalsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  // 選択中の type フィルタ（undefined = 全 type）。
  const [type, setType] = useState<SignalType | undefined>(undefined);

  useEffect(() => {
    // type が変わるたび取り直す。読み込み中表示のため一旦クリアする。
    setData(null);
    setError(null);
    getSignals(type ? { type } : undefined)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [type]);

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
        {error && (
          <div className="p-4 text-[13px] text-down">
            ⚠ 取得に失敗: {error}
            <div className="mt-1 text-[12px] text-ink-subtle">
              backend 起動と、夜間バッチ（POST /batch/run）の実行を確認するのだ。
            </div>
          </div>
        )}
        {!error && data === null && (
          <div className="p-4 text-[13px] text-ink-subtle">読み込み中…</div>
        )}
        {!error && data?.signals.length === 0 && (
          <div className="p-4 text-[13px] text-ink-subtle">
            まだシグナルがないのだ。`POST /batch/run` で夜間バッチを回すのだ。
          </div>
        )}
        {!error && data && data.signals.length > 0 && (
          <table className="w-full border-collapse">
            <thead>
              <tr>
                {[
                  { h: "コード / 銘柄", right: false },
                  { h: "スコア", right: true },
                  { h: "5日", right: true },
                  { h: "シグナル", right: false },
                ].map((c) => (
                  <th
                    key={c.h}
                    className={`h-8 border-hairline border-b px-2.5 font-medium text-[11px] text-ink-muted uppercase tracking-[0.3px] ${
                      c.right ? "text-right" : "text-left"
                    }`}
                  >
                    {c.h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.signals.map((s) => {
                const d5 = s.payload.change_5d;
                return (
                  <tr key={`${s.code}-${s.signal_type}`} className="hover:[&>td]:bg-surface-2">
                    <td className="h-[34px] border-hairline-soft border-b px-2.5 text-[13px]">
                      <Link href={`/stocks/${s.code}`} className="hover:text-accent">
                        <span className="num font-semibold text-accent">{s.code}</span>{" "}
                        <span className="text-[12px] text-ink-muted">{s.company_name ?? "—"}</span>
                      </Link>
                    </td>
                    <td className="h-[34px] border-hairline-soft border-b px-2.5 text-right text-[13px]">
                      <span className="inline-flex items-center justify-end gap-2">
                        <span className="h-1 w-12 overflow-hidden rounded-full bg-hairline">
                          <i
                            className="block h-full bg-accent"
                            style={{ width: `${s.score * 100}%` }}
                          />
                        </span>
                        <span className="num">{s.score.toFixed(2)}</span>
                      </span>
                    </td>
                    <td className="h-[34px] border-hairline-soft border-b px-2.5 text-right text-[13px]">
                      {d5 != null ? (
                        <span className={`num ${d5 >= 0 ? "text-up" : "text-down"}`}>
                          {d5 >= 0 ? "+" : ""}
                          {(d5 * 100).toFixed(1)}%
                        </span>
                      ) : (
                        <span className="text-ink-subtle">—</span>
                      )}
                    </td>
                    <td className="h-[34px] border-hairline-soft border-b px-2.5 text-[13px]">
                      <span className="rounded-sm bg-surface-2 px-1.5 py-0.5 text-[12px] text-ink-muted">
                        {s.payload.label ?? "—"}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>
    </>
  );
}
