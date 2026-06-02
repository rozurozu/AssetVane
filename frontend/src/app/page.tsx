import {
  allocation,
  journal,
  kpis,
  policy,
  proposals,
  signals,
  trendPath,
  watchlist,
} from "@/lib/mock-data";

// Dashboard（screens.md §3）。承認待ちの提案を主役に、資産概要・配分・方針・signals・
// watchlist・日記を密度優先で一望する。今はダミーデータ・配線なし。
export default function Dashboard() {
  return (
    <>
      <div className="mb-3 flex items-baseline justify-between">
        <div>
          <div className="font-semibold text-[20px] tracking-[-0.4px]">Dashboard</div>
          <div className="mt-0.5 text-[12px] text-ink-muted">
            夜の分析AI が 04:12 に更新 ・ 承認待ちの提案が 2 件
          </div>
        </div>
        <button type="button" className="text-[12px] text-accent">
          バッチを今すぐ実行
        </button>
      </div>

      {/* KPI 行 */}
      <div className="mb-3 grid grid-cols-5 gap-3 max-[1100px]:grid-cols-2">
        {kpis.map((k) => (
          <div key={k.label} className="rounded-lg border border-hairline bg-surface-1 p-3">
            <div className="flex items-center gap-1.5 font-medium text-[11px] text-ink-muted uppercase tracking-[0.2px]">
              {k.dot && <i className="h-[7px] w-[7px] rounded-sm" style={{ background: k.dot }} />}
              {k.label}
            </div>
            <div
              className={`num mt-2 font-semibold text-[22px] tracking-[-0.2px] ${k.tone === "up" ? "text-up" : ""}`}
            >
              {k.value}
            </div>
            <div
              className={`num mt-1 text-[11px] ${k.tone === "up" ? "text-up" : "text-ink-subtle"}`}
            >
              {k.sub}
            </div>
          </div>
        ))}
      </div>

      {/* 資産推移 ＋ 配分 */}
      <div className="mb-3 grid grid-cols-[2fr_1fr] gap-3 max-[1100px]:grid-cols-1">
        <Card title="資産推移" meta="12週遅延・約3か月前基準">
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
            <span>3か月前</span>
            <span>2か月前</span>
            <span>1か月前</span>
            <span>直近</span>
          </div>
        </Card>

        <Card title="配分" meta="policy 目標と対比">
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
                  <span className="num font-semibold">{a.pct}%</span>
                </div>
              ))}
              <div className="mt-1 flex items-center justify-between border-hairline-soft border-t pt-2 text-warning">
                <span>最大銘柄比率</span>
                <span className="num font-semibold">18.2% / 上限15%</span>
              </div>
            </div>
          </div>
        </Card>
      </div>

      {/* AI 提案 ＋ 現在の方針 */}
      <div className="mb-3 grid grid-cols-[3fr_2fr] gap-3 max-[1100px]:grid-cols-1">
        <Card
          title={
            <>
              夜の分析AI からの提案{" "}
              <span className="ml-1 rounded-sm bg-surface-2 px-1.5 py-0.5 font-medium text-[12px] text-warning">
                2 件 承認待ち
              </span>
            </>
          }
          link="提案履歴"
        >
          <div className="-mt-1 flex flex-col">
            {proposals.map((p) => (
              <div key={p.title} className="border-hairline-soft border-b py-2.5 last:border-b-0">
                <div className="flex items-center gap-2">
                  <span
                    className={`rounded-sm px-1.5 py-0.5 font-medium text-[12px] ${
                      p.kind === "POLICY" ? "bg-accent-weak text-accent" : "bg-up-weak text-up"
                    }`}
                  >
                    {p.kind}
                  </span>
                  <span className="font-semibold text-[13px]">{p.title}</span>
                </div>
                <div className="my-1.5 text-[13px] text-ink-muted leading-[1.45]">
                  {p.rationale}
                </div>
                <div className="flex gap-1.5">
                  <button
                    type="button"
                    className="rounded-md border border-accent bg-accent px-3 py-1.5 font-medium text-[13px] text-white"
                  >
                    承認
                  </button>
                  <button
                    type="button"
                    className="rounded-md border border-hairline bg-surface-2 px-3 py-1.5 font-medium text-[13px]"
                  >
                    却下
                  </button>
                  <button
                    type="button"
                    className="rounded-md px-3 py-1.5 font-medium text-[13px] text-ink-muted hover:text-ink"
                  >
                    根拠を見る
                  </button>
                </div>
              </div>
            ))}
          </div>
        </Card>

        <Card title="現在の投資方針" link="チャットで調整">
          <div className="rounded-md border border-hairline border-l-2 border-l-accent bg-canvas px-3 py-2.5 text-[13px] text-ink leading-[1.5]">
            {policy.rationale}
          </div>
          <div className="mt-3 grid grid-cols-3 gap-2">
            {policy.core.map((c) => (
              <div
                key={c.label}
                className="rounded-md border border-hairline bg-canvas px-2.5 py-2"
              >
                <span className="text-[11px] text-ink-muted">{c.label}</span>
                <b
                  className={`num mt-0.5 block font-semibold text-[15px] tracking-[-0.2px] ${c.warn ? "text-warning" : ""}`}
                >
                  {c.value}
                </b>
              </div>
            ))}
          </div>
          <div className="mt-3 border-hairline-soft border-t pt-2 text-[11px] text-ink-subtle">
            {policy.footer}
          </div>
        </Card>
      </div>

      {/* signals ＋ watchlist */}
      <div className="mb-3 grid grid-cols-[3fr_2fr] gap-3 max-[1100px]:grid-cols-1">
        <Card title="今日のシグナル（Trend Vane）" link="すべて">
          <Table head={["コード / 銘柄", "スコア", "5日", "シグナル"]} rightCols={[1, 2]}>
            {signals.map((s) => (
              <tr key={s.code} className="hover:[&>td]:bg-surface-2">
                <Td>
                  <span className="num font-semibold">{s.code}</span>{" "}
                  <span className="text-[12px] text-ink-muted">{s.name}</span>
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
                  <span className={`num ${s.up ? "text-up" : "text-down"}`}>{s.d5}</span>
                </Td>
                <Td>
                  <span className="rounded-sm bg-surface-2 px-1.5 py-0.5 text-[12px] text-ink-muted">
                    {s.sig}
                  </span>
                </Td>
              </tr>
            ))}
          </Table>
        </Card>

        <Card title="Watchlist ・ 調査ステータス" link="すべて">
          <Table head={["コード / 銘柄", "最終調査", ""]} rightCols={[1, 2]}>
            {watchlist.map((w) => (
              <tr key={w.code} className="hover:[&>td]:bg-surface-2">
                <Td>
                  <span className="num font-semibold">{w.code}</span>{" "}
                  <span className="text-[12px] text-ink-muted">{w.name}</span>
                </Td>
                <Td right>
                  <span
                    className={`num text-[12px] ${w.stale ? "text-warning" : "text-ink-subtle"}`}
                  >
                    {w.last}
                  </span>
                </Td>
                <Td right>
                  {w.action && (
                    <button
                      type="button"
                      className="rounded-md px-2 py-1 text-[13px] text-ink-muted hover:text-ink"
                    >
                      {w.action}
                    </button>
                  )}
                </Td>
              </tr>
            ))}
          </Table>
        </Card>
      </div>

      {/* 投資日記 */}
      <Card title="投資日記（最新）" meta="方針変更なし（前回 05-28）">
        <div className="num mb-1.5 font-medium text-[11px] text-accent">{journal.meta}</div>
        <p className="text-[13px] text-ink-muted leading-[1.55]">{journal.body}</p>
      </Card>
    </>
  );
}

// --- 汎用 UI（card / table）。density-first：罫線で区切り、余白は 12px に絞る ---
function Card({
  title,
  meta,
  link,
  children,
}: {
  title: React.ReactNode;
  meta?: string;
  link?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-lg border border-hairline bg-surface-1">
      <div className="flex items-center justify-between border-hairline border-b px-3 py-2">
        <h2 className="font-semibold text-[14px] tracking-[-0.1px]">{title}</h2>
        {meta && <span className="text-[11px] text-ink-subtle">{meta}</span>}
        {link && (
          <button type="button" className="text-[12px] text-accent">
            {link}
          </button>
        )}
      </div>
      <div className="p-3">{children}</div>
    </section>
  );
}

function Table({
  head,
  rightCols = [],
  children,
}: {
  head: string[];
  rightCols?: number[];
  children: React.ReactNode;
}) {
  return (
    <table className="w-full border-collapse">
      <thead>
        <tr>
          {head.map((h, i) => (
            <th
              // biome-ignore lint/suspicious/noArrayIndexKey: 固定ヘッダ
              key={i}
              className={`h-8 border-hairline border-b px-2.5 font-medium text-[11px] text-ink-muted uppercase tracking-[0.3px] ${
                rightCols.includes(i) ? "text-right" : "text-left"
              }`}
            >
              {h}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>{children}</tbody>
    </table>
  );
}

function Td({ children, right }: { children: React.ReactNode; right?: boolean }) {
  return (
    <td
      className={`h-[34px] border-hairline-soft border-b px-2.5 text-[13px] ${right ? "text-right" : ""}`}
    >
      {children}
    </td>
  );
}
