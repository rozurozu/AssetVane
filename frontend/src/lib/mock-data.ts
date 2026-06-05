// ナビ定義（Dashboard 用ダミーデータは Phase 1〜4 で全て実配線・mock は撤去済み）。
// kpis / allocation / trendPath は /asset-overview（Phase 2）、signals は /signals（Phase 1）、
// proposals / policy / journal は各 API（Phase 3）、watchlist は /watchlist（Phase 4）に配線済み。
// 設計: docs/api.md / docs/screens.md §3。

// href があるものは遷移可能（Next Link）。action があるものはトリガ（Advisor=チャット起動）。無いものは未投入 Phase（非活性）。
// アクティブ表示は Sidebar が usePathname() で判定する（ハードコードしない）。
// 任意フィールド（href/action/phase）を型に明示し、全項目が同型になるようにする
// （未投入 Phase のラベル＝phase を持つ項目が無くなっても Sidebar 側の型が unknown に落ちないように）。
type NavItem = {
  label: string;
  icon: string;
  href?: string;
  action?: string;
  phase?: string; // 未投入 Phase の注記（例 "P5"）。href/action が無い非活性項目に付ける
};
type NavSection = { group: string | null; items: NavItem[] };

export const nav: NavSection[] = [
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
      { label: "Watchlist", icon: "👁", href: "/watchlist" },
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
