// Dashboard 表示用のダミーデータ（Phase 3/4 まで配線なし）。
// kpis / allocation / trendPath は Phase 2 で /asset-overview に配線済み（app/page.tsx）。
// proposals / policy / journal / watchlist / signals は Phase 3/4 で本配線予定。
// 設計: docs/api.md / docs/screens.md §3。

// proposals / policy / journal の mock は削除（Dashboard が GET /proposals・/policy・/journal に実配線・spec §9.6）。
// signals / watchlist は Dashboard でまだモック表示なので残す（Phase 1/4 で本配線）。

export const signals = [
  { code: "6920", name: "レーザーテック", score: 0.88, d5: "+7.4%", up: true, sig: "25MA上抜け" },
  { code: "8035", name: "東京エレクトロン", score: 0.81, d5: "+5.1%", up: true, sig: "RSI反転" },
  { code: "6098", name: "リクルートHD", score: 0.74, d5: "+3.8%", up: true, sig: "25MA上抜け" },
  { code: "7203", name: "トヨタ自動車", score: 0.62, d5: "+1.9%", up: true, sig: "GC" },
  { code: "9984", name: "ソフトバンクG", score: 0.55, d5: "−2.3%", up: false, sig: "押し目" },
];

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
