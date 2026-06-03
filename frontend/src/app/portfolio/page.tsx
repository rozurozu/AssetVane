"use client";

// Portfolio ページ（screens.md #5・phase2-spec.md §6）。
// タブ「保有 / 入力」を上部に配置（OPEN-D 推奨タブ方式）。
// 保有タブ: 保有テーブル＋評価額カード（遅延注記）＋相関ヒートマップ＋メトリクスカード
//           ＋最適化ボタン→OptimizeTable＋資産推移スパークライン。
// 入力タブ: /transactions ページへ Link（TransactionForm・現金・外部資産は /transactions に）。
// DB には触れない。データ取得はすべて lib/api.ts 経由（ADR-005）。

import { CorrelationHeatmap } from "@/components/portfolio/CorrelationHeatmap";
import { OptimizeTable } from "@/components/portfolio/OptimizeTable";
import { TransactionForm } from "@/components/portfolio/TransactionForm";
import {
  type AssetOverview,
  type HoldingsResponse,
  type OptimizeResult,
  type PortfolioMetrics,
  type Stock,
  getAssetOverview,
  getHoldings,
  getPortfolioMetrics,
  getPortfolios,
  getStocks,
  optimizePortfolio,
} from "@/lib/api";
import { useEffect, useState } from "react";

type Tab = "holdings" | "input";

function fmtJpy(v: number | null): string {
  if (v == null) return "—";
  return `¥${v.toLocaleString("ja-JP", { maximumFractionDigits: 0 })}`;
}

function pct(v: number | null, digits = 1): string {
  if (v == null) return "—";
  return `${(v * 100).toFixed(digits)}%`;
}

// --- 汎用 UI（page.tsx の Card と同形・ここだけで使う）---
function Card({
  title,
  meta,
  children,
}: {
  title: React.ReactNode;
  meta?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-lg border border-hairline bg-surface-1">
      <div className="flex items-center justify-between border-hairline border-b px-3 py-2">
        <h2 className="font-semibold text-[14px] tracking-[-0.1px]">{title}</h2>
        {meta && <span className="text-[11px] text-ink-subtle">{meta}</span>}
      </div>
      <div className="p-3">{children}</div>
    </section>
  );
}

export default function PortfolioPage() {
  const [tab, setTab] = useState<Tab>("holdings");

  // ポートフォリオ ID（先頭＝既定・裁定 L-9）
  const [portfolioId, setPortfolioId] = useState<number | null>(null);

  // 保有
  const [holdings, setHoldings] = useState<HoldingsResponse | null>(null);
  const [holdingsErr, setHoldingsErr] = useState<string | null>(null);

  // メトリクス
  const [metrics, setMetrics] = useState<PortfolioMetrics | null>(null);
  const [metricsErr, setMetricsErr] = useState<string | null>(null);

  // 資産概要（スパークライン）
  const [overview, setOverview] = useState<AssetOverview | null>(null);

  // 最適化
  const [optimizeResult, setOptimizeResult] = useState<OptimizeResult | null>(null);
  const [optimizing, setOptimizing] = useState(false);
  const [optimizeErr, setOptimizeErr] = useState<string | null>(null);

  // 銘柄候補（TransactionForm 用）
  const [stocks, setStocks] = useState<Stock[]>([]);

  // ポートフォリオ ID を取得してから各データを取る
  useEffect(() => {
    getPortfolios()
      .then((ps) => {
        if (ps.length > 0) setPortfolioId(ps[0].portfolio_id);
      })
      .catch(() => setPortfolioId(1)); // 取得失敗時は既定 ID=1 で試みる

    // 銘柄候補は portfolio ID 無関係に取得
    getStocks()
      .then(setStocks)
      .catch(() => {});

    // 資産概要も並行取得
    getAssetOverview()
      .then(setOverview)
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (portfolioId == null) return;
    setHoldings(null);
    setHoldingsErr(null);
    getHoldings(portfolioId)
      .then(setHoldings)
      .catch((e) => setHoldingsErr(e instanceof Error ? e.message : String(e)));

    setMetrics(null);
    setMetricsErr(null);
    getPortfolioMetrics(portfolioId)
      .then(setMetrics)
      .catch((e) => setMetricsErr(e instanceof Error ? e.message : String(e)));
  }, [portfolioId]);

  async function handleOptimize() {
    if (portfolioId == null) return;
    setOptimizing(true);
    setOptimizeErr(null);
    setOptimizeResult(null);
    try {
      const r = await optimizePortfolio(portfolioId);
      setOptimizeResult(r);
    } catch (e) {
      setOptimizeErr(e instanceof Error ? e.message : String(e));
    } finally {
      setOptimizing(false);
    }
  }

  // 取引成功後に保有を最新化
  function handleTransactionDone(updated: HoldingsResponse) {
    setHoldings(updated);
    setOptimizeResult(null); // 最適化結果はリセット（保有が変わったため）
    setTab("holdings"); // 入力後は保有タブへ戻る
  }

  const vm = holdings?.valuation_meta;
  const delayNote =
    vm?.is_delayed && vm.as_of
      ? `J-Quants Free・12 週遅延・${vm.as_of} 基準`
      : vm?.as_of
        ? `${vm.as_of} 基準`
        : undefined;

  // スパークライン（overview.trend から SVG path を生成）
  const trendSvg = (() => {
    const pts = overview?.trend ?? [];
    if (pts.length < 2) return null;
    const vals = pts.map((p) => p.total_value);
    const minV = Math.min(...vals);
    const maxV = Math.max(...vals);
    const range = maxV - minV || 1;
    const W = 720;
    const H = 100;
    const d = pts
      .map((p, i) => {
        const x = (i / (pts.length - 1)) * W;
        const y = H - ((p.total_value - minV) / range) * (H - 10) - 5;
        return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(" ");
    return {
      d,
      lastX: W,
      lastY: (H - ((vals[vals.length - 1] - minV) / range) * (H - 10) - 5).toFixed(1),
    };
  })();

  // タブ定義
  const TABS: { key: Tab; label: string }[] = [
    { key: "holdings", label: "保有" },
    { key: "input", label: "入力" },
  ];

  return (
    <>
      <div className="mb-3 flex items-baseline justify-between">
        <div>
          <div className="font-semibold text-[20px] tracking-[-0.4px]">Portfolio</div>
          <div className="mt-0.5 text-[12px] text-ink-muted">
            {delayNote ?? "保有銘柄の管理・最適化・リバランス提案（Phase 2）"}
          </div>
        </div>
      </div>

      {/* タブ切替（保有 / 入力）*/}
      <div className="mb-3 flex gap-1">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => setTab(t.key)}
            className={`rounded-md px-3 py-1.5 text-[13px] font-medium ${
              tab === t.key
                ? "bg-surface-2 text-ink shadow-[inset_2px_0_0_var(--color-accent)]"
                : "text-ink-muted hover:bg-surface-2 hover:text-ink"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* ===== 保有タブ ===== */}
      {tab === "holdings" && (
        <div className="space-y-3">
          {/* 保有テーブル */}
          <Card title="保有銘柄" meta={delayNote}>
            {holdingsErr && (
              <div className="text-[13px] text-down">⚠ 取得に失敗: {holdingsErr}</div>
            )}
            {!holdingsErr && holdings === null && (
              <div className="text-[13px] text-ink-subtle">読み込み中…</div>
            )}
            {!holdingsErr && holdings?.holdings.length === 0 && (
              <div className="text-[13px] text-ink-subtle">
                保有銘柄がないのだ。「入力」タブから取引を記録するのだ。
              </div>
            )}
            {!holdingsErr && holdings && holdings.holdings.length > 0 && (
              <table className="w-full border-collapse">
                <thead>
                  <tr>
                    {[
                      { h: "コード / 銘柄", right: false },
                      { h: "株数", right: true },
                      { h: "平均取得", right: true },
                      { h: "現値", right: true },
                      { h: "評価額", right: true },
                      { h: "含み損益", right: true },
                      { h: "比率", right: true },
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
                  {holdings.holdings.map((h) => {
                    const pnlPos = h.unrealized_pnl != null && h.unrealized_pnl >= 0;
                    return (
                      <tr key={h.id} className="hover:[&>td]:bg-surface-2">
                        <td className="h-[34px] border-hairline-soft border-b px-2.5 text-[13px]">
                          <span className="num font-semibold text-accent">{h.code}</span>{" "}
                          <span className="text-[12px] text-ink-muted">
                            {h.company_name ?? "—"}
                          </span>
                        </td>
                        <td className="num h-[34px] border-hairline-soft border-b px-2.5 text-right text-[13px]">
                          {h.shares.toLocaleString("ja-JP")}
                        </td>
                        <td className="num h-[34px] border-hairline-soft border-b px-2.5 text-right text-[13px] text-ink-muted">
                          {fmtJpy(h.avg_cost)}
                        </td>
                        <td className="num h-[34px] border-hairline-soft border-b px-2.5 text-right text-[13px]">
                          {fmtJpy(h.last_close)}
                        </td>
                        <td className="num h-[34px] border-hairline-soft border-b px-2.5 text-right font-semibold text-[13px]">
                          {fmtJpy(h.market_value)}
                        </td>
                        <td
                          className={`num h-[34px] border-hairline-soft border-b px-2.5 text-right font-semibold text-[13px] ${
                            h.unrealized_pnl == null
                              ? "text-ink-subtle"
                              : pnlPos
                                ? "text-up"
                                : "text-down"
                          }`}
                        >
                          {h.unrealized_pnl != null
                            ? `${pnlPos ? "+" : ""}${fmtJpy(h.unrealized_pnl)}`
                            : "—"}
                        </td>
                        <td className="num h-[34px] border-hairline-soft border-b px-2.5 text-right text-[13px]">
                          {pct(h.weight)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </Card>

          {/* 相関ヒートマップ */}
          <Card
            title="相関ヒートマップ"
            meta={metrics?.is_delayed && metrics.as_of ? `${metrics.as_of} 基準` : undefined}
          >
            {metricsErr && <div className="text-[13px] text-down">⚠ 取得に失敗: {metricsErr}</div>}
            {!metricsErr && metrics === null && (
              <div className="text-[13px] text-ink-subtle">読み込み中…</div>
            )}
            {!metricsErr && metrics && <CorrelationHeatmap data={metrics.correlation} />}
          </Card>

          {/* メトリクスカード（シャープ / 年率リターン / 最大DD）*/}
          {metrics && (
            <div className="grid grid-cols-3 gap-3 max-[800px]:grid-cols-1">
              {[
                {
                  label: "年率リターン",
                  value: pct(metrics.annual_return),
                  tone:
                    metrics.annual_return != null
                      ? metrics.annual_return >= 0
                        ? "up"
                        : "down"
                      : null,
                },
                {
                  label: "年率ボラティリティ",
                  value: pct(metrics.annual_volatility),
                  tone: null,
                },
                {
                  label: "シャープ比",
                  value: metrics.sharpe != null ? metrics.sharpe.toFixed(2) : "—",
                  tone: null,
                },
                {
                  label: "最大ドローダウン",
                  value: pct(metrics.max_drawdown),
                  tone: "down" as const,
                },
                {
                  label: "参照期間",
                  value: metrics.lookback_days != null ? `${metrics.lookback_days} 営業日` : "—",
                  tone: null,
                },
              ].map((c) => (
                <div key={c.label} className="rounded-lg border border-hairline bg-surface-1 p-3">
                  <div className="text-[11px] text-ink-muted">{c.label}</div>
                  <div
                    className={`num mt-1 font-semibold text-[18px] tracking-[-0.2px] ${
                      c.tone === "up" ? "text-up" : c.tone === "down" ? "text-down" : ""
                    }`}
                  >
                    {c.value}
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* 逸脱警告（policy 違反）*/}
          {metrics && metrics.deviations.filter((d) => d.breached).length > 0 && (
            <Card title="逸脱（policy 違反）">
              <div className="space-y-1.5">
                {metrics.deviations
                  .filter((d) => d.breached)
                  .map((d) => (
                    <div
                      key={d.kind}
                      className="flex items-center justify-between rounded-md border border-warning bg-canvas px-3 py-2"
                    >
                      <span className="text-[13px] text-warning">{d.label}</span>
                      <span className="num text-[13px] font-semibold text-warning">
                        {pct(d.current)} / 上限 {pct(d.limit)}
                      </span>
                    </div>
                  ))}
              </div>
            </Card>
          )}

          {/* 最適化 */}
          <Card title="最適比率（平均分散最適化）">
            {!optimizeResult && (
              <div className="space-y-2">
                <div className="text-[12px] text-ink-muted">
                  policy 制約（現金目標・1
                  銘柄上限・業種上限）を使い、シャープ比を最大化する最適比率を提案するのだ。
                </div>
                {optimizeErr && (
                  <div className="text-[13px] text-down">⚠ 最適化に失敗: {optimizeErr}</div>
                )}
                <button
                  type="button"
                  onClick={handleOptimize}
                  disabled={optimizing || portfolioId == null}
                  className="rounded-md border border-accent bg-accent px-4 py-1.5 font-semibold text-[13px] text-white disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {optimizing ? "計算中…" : "最適化を実行するのだ"}
                </button>
              </div>
            )}
            {optimizeResult && (
              <div className="space-y-2">
                <OptimizeTable result={optimizeResult} />
                <button
                  type="button"
                  onClick={() => setOptimizeResult(null)}
                  className="text-[12px] text-ink-muted hover:text-ink"
                >
                  クリア
                </button>
              </div>
            )}
          </Card>

          {/* 資産推移スパークライン */}
          {overview && overview.trend.length >= 2 && trendSvg && (
            <Card
              title="資産推移"
              meta={
                overview.is_delayed && overview.as_of
                  ? `12 週遅延・${overview.as_of} 基準`
                  : (overview.as_of ?? undefined)
              }
            >
              <svg
                role="img"
                viewBox="0 0 720 110"
                width="100%"
                height={110}
                preserveAspectRatio="none"
                aria-label="資産推移"
              >
                <defs>
                  <linearGradient id="pf" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="rgba(0,153,255,.22)" />
                    <stop offset="100%" stopColor="rgba(0,153,255,0)" />
                  </linearGradient>
                </defs>
                <path d={`${trendSvg.d} L720,110 L0,110 Z`} fill="url(#pf)" />
                <path d={trendSvg.d} fill="none" stroke="var(--color-accent)" strokeWidth={1.8} />
                <circle
                  cx={trendSvg.lastX}
                  cy={Number(trendSvg.lastY)}
                  r={3}
                  fill="var(--color-accent)"
                />
              </svg>
              <div className="num mt-1.5 flex justify-between text-[11px] text-ink-subtle">
                <span>{overview.trend[0]?.date}</span>
                <span>{overview.trend[overview.trend.length - 1]?.date}</span>
              </div>
            </Card>
          )}
        </div>
      )}

      {/* ===== 入力タブ ===== */}
      {tab === "input" && (
        <div className="space-y-3">
          <Card title="取引を記録するのだ">
            {portfolioId != null && (
              <TransactionForm
                portfolioId={portfolioId}
                stocks={stocks}
                onDone={handleTransactionDone}
              />
            )}
            {portfolioId == null && (
              <div className="text-[13px] text-ink-subtle">ポートフォリオを読み込み中…</div>
            )}
          </Card>
        </div>
      )}
    </>
  );
}
