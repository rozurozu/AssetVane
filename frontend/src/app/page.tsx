"use client";

// Dashboard（screens.md §3）。承認待ちの提案を主役に、資産概要・配分・方針・signals・
// watchlist・日記を密度優先で一望する。
// KPI / allocation / 資産推移 は getAssetOverview() の実データに配線（Phase 2）。
// policy / proposals / journal は getPolicy() / getProposals("pending") / getJournal() に配線（Phase 3）。
// signals は getSignals(limit:5) の実データに配線（Phase 1・screens.md §3「今日の signals は /signals」）。
// watchlist は getWatchlist() の実データに配線（Phase 4・screens.md §3「watchlist 再調査」）。
// backend 未起動でも壊れないよう fetch 失敗は握って空表示＋注記にする（spec §9.6）。

import { GeneralNewsWidget } from "@/components/general-news/GeneralNewsWidget";
import { Card } from "@/components/ui/Card";
import { DataTable, Td } from "@/components/ui/DataTable";
import {
  type AssetOverview,
  type Deviation,
  type GeneralNewsResponse,
  type JournalEntry,
  type Policy,
  type Proposal,
  type Signal,
  type WatchlistItem,
  getAssetOverview,
  getGeneralNews,
  getJournal,
  getPolicy,
  getProposals,
  getSignals,
  getWatchlist,
  runBatch,
} from "@/lib/api";
import { fmtJpy, pct } from "@/lib/format";
import Link from "next/link";
import { useEffect, useState } from "react";

export default function Dashboard() {
  const [overview, setOverview] = useState<AssetOverview | null>(null);
  const [overviewErr, setOverviewErr] = useState<string | null>(null);
  // Phase 3 実配線（fetch 失敗は握って空表示・spec §9.6）。
  const [policy, setPolicy] = useState<Policy | null>(null);
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [journalLatest, setJournalLatest] = useState<JournalEntry | null>(null);
  // Phase 1 signals（上位 5 件・score 降順は backend 既定）。
  const [signals, setSignals] = useState<Signal[]>([]);
  const [signalsDelayed, setSignalsDelayed] = useState(false);
  // Phase 4 watchlist（実配線・上位 5 件のみ表示・古い順は気にせず一覧の先頭）。
  const [watchlist, setWatchlist] = useState<WatchlistItem[]>([]);
  // ADR-034 一般ニュース（カテゴリ別・fetch 失敗は握って空表示）。
  const [generalNews, setGeneralNews] = useState<GeneralNewsResponse | null>(null);
  // 「バッチを今すぐ実行」ボタンの状態（202 受付・進捗は Discord/fetch_meta で追う）。
  const [batchBusy, setBatchBusy] = useState(false);
  const [batchNote, setBatchNote] = useState<string | null>(null);

  useEffect(() => {
    getAssetOverview()
      .then(setOverview)
      .catch((e) => setOverviewErr(e instanceof Error ? e.message : String(e)));
    // policy / proposals / journal / signals は失敗しても画面を壊さない（空表示）。
    getPolicy()
      .then(setPolicy)
      .catch(() => {});
    getProposals("pending")
      .then((r) => setProposals(r.proposals))
      .catch(() => {});
    getJournal()
      .then((r) => setJournalLatest(r.entries[0] ?? null))
      .catch(() => {});
    getSignals({ limit: 5 })
      .then((r) => {
        setSignals(r.signals);
        setSignalsDelayed(r.is_delayed);
      })
      .catch(() => {});
    getWatchlist()
      .then((r) => setWatchlist(r.items))
      .catch(() => {});
    getGeneralNews()
      .then(setGeneralNews)
      .catch(() => {});
  }, []);

  // バッチを手動起動（POST /batch/run）。202 受付なので「起動した」までを伝え、進捗は追わない。
  async function onRunBatch() {
    setBatchBusy(true);
    setBatchNote(null);
    try {
      await runBatch();
      setBatchNote("バッチを起動したのだ。進捗は signals/資産が更新されるまで待つのだ。");
    } catch (e) {
      setBatchNote(e instanceof Error ? e.message : String(e));
    } finally {
      setBatchBusy(false);
    }
  }

  // 遅延注記（is_delayed=true かつ as_of がある場合に表示）
  const delayNote =
    overview?.is_delayed && overview.as_of
      ? `12 週遅延・${overview.as_of} 基準`
      : overview?.as_of
        ? `${overview.as_of} 基準`
        : "12 週遅延";

  // 実データから KPI 行を構築（null のときはモックの構造を踏襲しつつ「—」表示）
  type KpiItem = { label: string; value: string; sub: string; tone?: "up" | "down"; dot?: string };
  const kpis: KpiItem[] = overview
    ? (() => {
        const stock = overview.allocation.find((a) => a.name === "株式");
        const cash = overview.allocation.find((a) => a.name === "現金");
        const ext = overview.allocation.find((a) => a.name === "投信");
        return [
          {
            label: "総資産",
            value: fmtJpy(overview.total_value),
            sub: `${overview.pnl >= 0 ? "▲ " : "▼ "}${fmtJpy(overview.pnl)}（${overview.total_value > 0 ? `${((overview.pnl / (overview.total_value - overview.pnl)) * 100).toFixed(2)}%` : "—"}）`,
            tone: (overview.pnl >= 0 ? "up" : "down") as "up" | "down",
          },
          {
            label: "株式",
            value: fmtJpy(overview.stock_value),
            sub: stock ? pct(stock.weight) : "—",
            dot: "var(--color-chart-1)",
          },
          {
            label: "現金",
            value: fmtJpy(overview.cash_value),
            sub: cash
              ? `${pct(cash.weight)} ・ 目標 ${overview.policy_targets.target_cash_ratio != null ? pct(overview.policy_targets.target_cash_ratio) : "—"}`
              : "—",
            dot: "var(--color-chart-2)",
          },
          {
            label: "投信",
            value: fmtJpy(overview.external_value),
            sub: ext ? pct(ext.weight) : "—",
            dot: "var(--color-chart-4)",
          },
          {
            label: "評価損益",
            value: `${overview.pnl >= 0 ? "+" : ""}${fmtJpy(overview.pnl)}`,
            sub: overview.pnl >= 0 ? "含み益" : "含み損",
            tone: (overview.pnl >= 0 ? "up" : "down") as "up" | "down",
          },
        ] satisfies KpiItem[];
      })()
    : ([
        { label: "総資産", value: "—", sub: "データ取得中" },
        { label: "株式", value: "—", sub: "—", dot: "var(--color-chart-1)" },
        { label: "現金", value: "—", sub: "—", dot: "var(--color-chart-2)" },
        { label: "投信", value: "—", sub: "—", dot: "var(--color-chart-4)" },
        { label: "評価損益", value: "—", sub: "—" },
      ] satisfies KpiItem[]);

  // 配分ドーナツ（allocation.weight × 100 でドーナツ座標を計算）
  type DonutSlice = { name: string; pct: number; color: string; dash: string; offset: number };
  const allocation: DonutSlice[] = overview
    ? (() => {
        const colors: Record<string, string> = {
          株式: "var(--color-chart-1)",
          現金: "var(--color-chart-2)",
          投信: "var(--color-chart-4)",
        };
        let cumPct = 0;
        return overview.allocation.map((a) => {
          const pctNum = a.weight * 100;
          const offset = -(cumPct - 25); // strokeDashoffset の起点は 12 時方向（-25）
          cumPct += pctNum;
          return {
            name: a.name,
            pct: pctNum,
            color: colors[a.name] ?? "var(--color-chart-5)",
            dash: `${pctNum} ${100 - pctNum}`,
            offset,
          };
        });
      })()
    : [];

  // 逸脱（deviations.breached のもの）
  const breachedDeviations: Deviation[] = overview?.deviations.filter((d) => d.breached) ?? [];

  // 資産推移 SVG path（overview.trend から計算）
  const trendPath = (() => {
    const pts = overview?.trend ?? [];
    if (pts.length < 2) return null;
    const vals = pts.map((p) => p.total_value);
    const minV = Math.min(...vals);
    const maxV = Math.max(...vals);
    const range = maxV - minV || 1;
    const W = 720;
    const H = 120;
    return pts
      .map((p, i) => {
        const x = (i / (pts.length - 1)) * W;
        const y = H - ((p.total_value - minV) / range) * (H - 20) - 10;
        return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(" ");
  })();

  const hasOverview = overview != null && overview.total_value > 0;

  return (
    <>
      <div className="mb-3 flex items-baseline justify-between">
        <div>
          <div className="font-semibold text-[20px] tracking-[-0.4px]">Dashboard</div>
          <div className="mt-0.5 text-[12px] text-ink-muted">
            {journalLatest ? `夜の分析AI が ${journalLatest.date} に更新 ・ ` : "夜の分析AI ・ "}
            承認待ちの提案が {proposals.length} 件
          </div>
        </div>
        <button
          type="button"
          onClick={onRunBatch}
          disabled={batchBusy}
          className="text-[12px] text-accent disabled:text-ink-subtle"
        >
          {batchBusy ? "起動中…" : "バッチを今すぐ実行"}
        </button>
      </div>

      {batchNote && (
        <div className="mb-3 rounded-lg border border-hairline bg-surface-1 p-3 text-[13px] text-ink-muted">
          {batchNote}
        </div>
      )}

      {/* データ未投入（total_value=0 または取得エラー）*/}
      {overviewErr && (
        <div className="mb-3 rounded-lg border border-hairline bg-surface-1 p-3 text-[13px] text-ink-muted">
          ⚠ 資産データの取得に失敗: {overviewErr}
          <div className="mt-1 text-[12px] text-ink-subtle">
            backend が起動しているか確認するのだ。
          </div>
        </div>
      )}
      {!overviewErr && overview && !hasOverview && (
        <div className="mb-3 rounded-lg border border-hairline bg-surface-1 p-3 text-[13px] text-ink-muted">
          資産データが未投入のだ。
          <Link href="/portfolio?tab=input" className="ml-1 text-accent hover:underline">
            Portfolio の入力タブ
          </Link>{" "}
          から取引・現金・外部資産を登録するのだ。
        </div>
      )}

      {/* KPI 行 */}
      <div className="mb-3 grid grid-cols-5 gap-3 max-[1100px]:grid-cols-2">
        {kpis.map((k) => (
          <div key={k.label} className="rounded-lg border border-hairline bg-surface-1 p-3">
            <div className="flex items-center gap-1.5 font-medium text-[11px] text-ink-muted uppercase tracking-[0.2px]">
              {k.dot && <i className="h-[7px] w-[7px] rounded-sm" style={{ background: k.dot }} />}
              {k.label}
            </div>
            <div
              className={`num mt-2 font-semibold text-[22px] tracking-[-0.2px] ${k.tone === "up" ? "text-up" : k.tone === "down" ? "text-down" : ""}`}
            >
              {k.value}
            </div>
            <div
              className={`num mt-1 text-[11px] ${k.tone === "up" ? "text-up" : k.tone === "down" ? "text-down" : "text-ink-subtle"}`}
            >
              {k.sub}
            </div>
          </div>
        ))}
      </div>

      {/* 逸脱警告（policy 違反） */}
      {breachedDeviations.length > 0 && (
        <div className="mb-3 flex flex-wrap gap-2">
          {breachedDeviations.map((d) => (
            <div
              key={d.kind}
              className="flex items-center gap-2 rounded-md border border-warning bg-canvas px-3 py-1.5"
            >
              <span className="text-[12px] text-warning font-semibold">{d.label}</span>
              <span className="num text-[12px] text-warning">
                {pct(d.current)} / 上限 {pct(d.limit)}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* 資産推移 ＋ 配分 */}
      <div className="mb-3 grid grid-cols-[2fr_1fr] gap-3 max-[1100px]:grid-cols-1">
        <Card title="資産推移" meta={delayNote}>
          {trendPath ? (
            <>
              <svg
                role="img"
                viewBox="0 0 720 130"
                width="100%"
                height={130}
                preserveAspectRatio="none"
                aria-label="資産推移"
              >
                <defs>
                  <linearGradient id="f" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="rgba(0,153,255,.22)" />
                    <stop offset="100%" stopColor="rgba(0,153,255,0)" />
                  </linearGradient>
                </defs>
                <line x1="0" y1="33" x2="720" y2="33" stroke="#1a1a1a" />
                <line x1="0" y1="76" x2="720" y2="76" stroke="#1a1a1a" />
                <path d={`${trendPath} L720,130 L0,130 Z`} fill="url(#f)" />
                <path d={trendPath} fill="none" stroke="var(--color-accent)" strokeWidth={1.8} />
                <circle cx="720" cy="20" r="3" fill="var(--color-accent)" />
              </svg>
              <div className="num mt-1.5 flex justify-between text-[11px] text-ink-subtle">
                <span>{overview?.trend[0]?.date}</span>
                <span>{overview?.trend[overview.trend.length - 1]?.date}</span>
              </div>
            </>
          ) : (
            <div className="flex h-[130px] items-center justify-center text-[13px] text-ink-subtle">
              {overview ? "資産スナップショットが蓄積されると表示されるのだ" : "読み込み中…"}
            </div>
          )}
        </Card>

        <Card title="配分" meta="policy 目標と対比">
          {allocation.length > 0 ? (
            <div className="flex items-center gap-4">
              <svg role="img" width={104} height={104} viewBox="0 0 42 42" aria-label="資産配分">
                <circle
                  cx="21"
                  cy="21"
                  r="15.9"
                  fill="none"
                  stroke="var(--color-hairline)"
                  strokeWidth={5}
                />
                {allocation.map((a) => (
                  <circle
                    key={a.name}
                    cx="21"
                    cy="21"
                    r="15.9"
                    fill="none"
                    stroke={a.color}
                    strokeWidth={5}
                    strokeDasharray={a.dash}
                    strokeDashoffset={a.offset}
                  />
                ))}
              </svg>
              <div className="flex flex-1 flex-col gap-1.5">
                {allocation.map((a) => (
                  <div key={a.name} className="flex items-center justify-between text-[13px]">
                    <span className="flex items-center gap-2 text-ink-muted">
                      <i className="h-2 w-2 rounded-sm" style={{ background: a.color }} />
                      {a.name}
                    </span>
                    <span className="num font-semibold">{a.pct.toFixed(1)}%</span>
                  </div>
                ))}
                {/* policy 逸脱がある場合に配分カードにも警告を出す */}
                {breachedDeviations.length > 0 && (
                  <div className="mt-1 flex items-center justify-between border-hairline-soft border-t pt-2 text-warning">
                    <span className="text-[12px]">{breachedDeviations[0].label}</span>
                    <span className="num text-[12px] font-semibold">
                      {pct(breachedDeviations[0].current)} / 上限 {pct(breachedDeviations[0].limit)}
                    </span>
                  </div>
                )}
              </div>
            </div>
          ) : (
            <div className="flex h-[104px] items-center justify-center text-[13px] text-ink-subtle">
              {overview ? "資産を登録すると表示されるのだ" : "読み込み中…"}
            </div>
          )}
        </Card>
      </div>

      {/* AI 提案 ＋ 現在の方針（Phase 3 実配線）*/}
      <div className="mb-3 grid grid-cols-[3fr_2fr] gap-3 max-[1100px]:grid-cols-1">
        <Card
          title={
            <>
              夜の分析AI からの提案{" "}
              <span className="ml-1 rounded-sm bg-surface-2 px-1.5 py-0.5 font-medium text-[12px] text-warning">
                {proposals.length} 件 承認待ち
              </span>
            </>
          }
          link={
            <Link href="/proposals" className="text-[12px] text-accent">
              提案履歴
            </Link>
          }
        >
          {proposals.length === 0 ? (
            <div className="py-4 text-center text-[13px] text-ink-subtle">
              承認待ちの提案はないのだ。
            </div>
          ) : (
            <div className="-mt-1 flex flex-col">
              {proposals.map((p) => (
                <div key={p.id} className="border-hairline-soft border-b py-2.5 last:border-b-0">
                  <div className="flex items-center gap-2">
                    <span
                      className={`rounded-sm px-1.5 py-0.5 font-medium text-[12px] ${
                        p.kind === "policy_change"
                          ? "bg-accent-weak text-accent"
                          : p.kind === "sell"
                            ? "bg-down-weak text-down"
                            : "bg-up-weak text-up"
                      }`}
                    >
                      {p.kind}
                    </span>
                    <span className="num text-[11px] text-ink-subtle">#{p.id}</span>
                  </div>
                  {p.rationale && (
                    <div className="my-1.5 text-[13px] text-ink-muted leading-[1.45]">
                      {p.rationale}
                    </div>
                  )}
                  <Link href="/proposals" className="text-[12px] text-accent hover:underline">
                    承認/却下するのだ →
                  </Link>
                </div>
              ))}
            </div>
          )}
        </Card>

        <Card
          title="現在の投資方針"
          link={
            <Link href="/policy" className="text-[12px] text-accent">
              方針を編集
            </Link>
          }
        >
          {policy ? (
            <>
              {policy.rationale && (
                <div className="rounded-md border border-hairline border-l-2 border-l-accent bg-canvas px-3 py-2.5 text-[13px] text-ink leading-[1.5]">
                  {policy.rationale}
                </div>
              )}
              <div className="mt-3 grid grid-cols-3 gap-2">
                {(() => {
                  const c = policy.core;
                  const items: { label: string; value: string; warn?: boolean }[] = [
                    { label: "リスク許容度", value: c.risk_tolerance ?? "—" },
                    { label: "時間軸", value: c.time_horizon ?? "—" },
                    { label: "現金目標", value: pct(c.target_cash_ratio, 0) },
                    { label: "1銘柄上限", value: pct(c.max_position_weight, 0) },
                    { label: "目標リターン", value: pct(c.target_return, 0) },
                    {
                      label: "レバレッジ",
                      value: c.no_leverage ? "不可" : "可",
                      warn: c.no_leverage,
                    },
                  ];
                  return items.map((it) => (
                    <div
                      key={it.label}
                      className="rounded-md border border-hairline bg-canvas px-2.5 py-2"
                    >
                      <span className="text-[11px] text-ink-muted">{it.label}</span>
                      <b
                        className={`num mt-0.5 block font-semibold text-[15px] tracking-[-0.2px] ${it.warn ? "text-warning" : ""}`}
                      >
                        {it.value}
                      </b>
                    </div>
                  ));
                })()}
              </div>
              <div className="mt-3 border-hairline-soft border-t pt-2 text-[11px] text-ink-subtle">
                除外:{" "}
                {policy.core.exclusions.length > 0 ? policy.core.exclusions.join(", ") : "なし"}
                {policy.updated_at && ` ・ 最終更新 ${policy.updated_at.slice(0, 10)}`}
              </div>
            </>
          ) : (
            <div className="py-4 text-center text-[13px] text-ink-subtle">
              方針が未設定なのだ。
              <Link href="/policy" className="ml-1 text-accent hover:underline">
                方針を設定
              </Link>
            </div>
          )}
        </Card>
      </div>

      {/* signals（Phase 1 実配線）＋ watchlist（Phase 4 までモック）*/}
      <div className="mb-3 grid grid-cols-[3fr_2fr] gap-3 max-[1100px]:grid-cols-1">
        <Card
          title="今日のシグナル（Trend Vane）"
          meta={signalsDelayed ? "12週遅延" : undefined}
          link={
            <Link href="/signals" className="text-[12px] text-accent">
              すべて
            </Link>
          }
        >
          {signals.length === 0 ? (
            <div className="py-4 text-center text-[13px] text-ink-subtle">
              まだシグナルがないのだ。夜間バッチ（上の「バッチを今すぐ実行」）で算出されるのだ。
            </div>
          ) : (
            <DataTable
              columns={[
                { label: "コード / 銘柄" },
                { label: "スコア", right: true },
                { label: "5日", right: true },
                { label: "シグナル" },
              ]}
            >
              {signals.map((s) => {
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
        </Card>

        <Card
          title="Watchlist ・ 調査ステータス"
          link={
            <Link href="/watchlist" className="text-[12px] text-accent">
              すべて
            </Link>
          }
        >
          {watchlist.length === 0 ? (
            <div className="py-4 text-center text-[13px] text-ink-subtle">
              監視銘柄がないのだ。
              <Link href="/watchlist" className="ml-1 text-accent hover:underline">
                Watchlist
              </Link>{" "}
              から追加するのだ。
            </div>
          ) : (
            <DataTable
              columns={[
                { label: "コード / 銘柄" },
                { label: "最終調査", right: true },
                { label: "", right: true },
              ]}
            >
              {/* 上位 5 件のみ表示（全件は /watchlist へ）。調査操作も /watchlist で行う。 */}
              {watchlist.slice(0, 5).map((w) => (
                <tr key={w.id} className="hover:[&>td]:bg-surface-2">
                  <Td>
                    <Link href={`/stocks/${w.code}`} className="hover:text-accent">
                      <span className="num font-semibold text-accent">{w.code}</span>{" "}
                      <span className="text-[12px] text-ink-muted">{w.company_name ?? "—"}</span>
                    </Link>
                  </Td>
                  <Td right>
                    <span
                      className={`num text-[12px] ${w.stale ? "text-warning" : "text-ink-subtle"}`}
                    >
                      {w.last_investigated_at ? w.last_investigated_at.slice(0, 10) : "未調査"}
                    </span>
                  </Td>
                  <Td right>
                    {(w.stale || w.last_investigated_at == null) && (
                      <Link
                        href="/watchlist"
                        className="rounded-md px-2 py-1 text-[12px] text-warning hover:text-ink"
                      >
                        {w.last_investigated_at == null ? "調査" : "再調査"}
                      </Link>
                    )}
                  </Td>
                </tr>
              ))}
            </DataTable>
          )}
        </Card>
      </div>

      {/* 一般ニュース（ADR-034・市況/マクロ/世界情勢をカテゴリ別に眺める）*/}
      <div className="mb-3">
        <GeneralNewsWidget data={generalNews} />
      </div>

      {/* 投資日記（Phase 3 実配線・最新 1 件）*/}
      <Card
        title="投資日記（最新）"
        link={
          <Link href="/journal" className="text-[12px] text-accent">
            すべて
          </Link>
        }
      >
        {journalLatest ? (
          <>
            <div className="num mb-1.5 font-medium text-[11px] text-accent">
              {journalLatest.date} ・{" "}
              {journalLatest.source === "nightly" ? "夜の分析" : "チャット要約"}
              {journalLatest.llm_model ? ` ・ ${journalLatest.llm_model}` : ""}
            </div>
            <p className="text-[13px] text-ink-muted leading-[1.55]">
              {journalLatest.observations ?? journalLatest.proposal ?? "（本文なし）"}
            </p>
          </>
        ) : (
          <p className="text-[13px] text-ink-subtle">
            まだ日記がないのだ。夜間バッチで生成されるのだ。
          </p>
        )}
      </Card>
    </>
  );
}
