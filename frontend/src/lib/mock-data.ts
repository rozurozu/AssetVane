// Dashboard 表示用のダミーデータ（残るは watchlist のみ・Phase 4 で本配線）。
// kpis / allocation / trendPath は /asset-overview（Phase 2）、signals は /signals（Phase 1）、
// proposals / policy / journal は各 API（Phase 3）に配線済み（app/page.tsx）。
// 設計: docs/api.md / docs/screens.md §3。

// proposals / policy / journal / signals の mock は削除（Dashboard が GET /proposals・/policy・
// /journal・/signals に実配線・spec §9.6）。watchlist のみ Phase 4（ドシエ）まで残す。

export const watchlist = [
  { code: "6857", name: "アドバンテスト", last: "2日前", stale: false },
  { code: "6758", name: "ソニーグループ", last: "5日前", stale: false },
  { code: "4063", name: "信越化学", last: "23日前", stale: true, action: "再調査" },
  { code: "9433", name: "KDDI", last: "未調査", stale: true, action: "調査" },
];

// href があるものは遷移可能（Next Link）。action があるものはトリガ（Advisor=チャット起動）。無いものは未投入 Phase（非活性）。
// アクティブ表示は Sidebar が usePathname() で判定する（ハードコードしない）。
export const nav = [
  {
    group: null,
    items: [
      { label: "Dashboard", icon: "▦", href: "/" },
      { label: "Stocks", icon: "≣", href: "/stocks" },
    ],
  },
  {
    group: "分析",
    items: [
      { label: "Signals", icon: "📈", href: "/signals" },
      { label: "Portfolio", icon: "⚖", href: "/portfolio" },
      { label: "Watchlist", icon: "👁", phase: "P4" },
    ],
  },
  {
    group: "Advisor",
    items: [
      // Advisor は専用ページを作らずチャット起動トリガ（onClick で open・OPEN-I・spec §9.6）。
      { label: "Advisor", icon: "🧠", action: "advisor" },
      { label: "Policy", icon: "🧭", href: "/policy" },
      { label: "Journal", icon: "📓", href: "/journal" },
      { label: "Proposals", icon: "✓", href: "/proposals" },
    ],
  },
  { group: "システム", items: [{ label: "Settings", icon: "⚙" }] },
];
