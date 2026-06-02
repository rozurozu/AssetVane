// Dashboard 表示用のダミーデータ（配線なし）。
// 後で REST（/asset-overview, /signals, /proposals, /policy, /journal …）に差し替える。
// 設計: docs/api.md / docs/screens.md §3。

export const kpis = [
  { label: "総資産", value: "¥4,820,000", sub: "▲ +¥312,000 (+6.92%)", tone: "up" as const },
  { label: "株式", value: "¥3,640,000", sub: "75.5% ・ 8銘柄", dot: "var(--color-chart-1)" },
  { label: "現金", value: "¥980,000", sub: "20.3% ・ 目標 25%", dot: "var(--color-chart-2)" },
  { label: "投信", value: "¥200,000", sub: "4.1%", dot: "var(--color-chart-4)" },
  { label: "評価損益", value: "+¥312,000", sub: "含み益", tone: "up" as const },
];

export const allocation = [
  { name: "株式", pct: 75.5, color: "var(--color-chart-1)", dash: "75.5 24.5", offset: 25 },
  { name: "現金", pct: 20.3, color: "var(--color-chart-2)", dash: "20.3 79.7", offset: -50.5 },
  { name: "投信", pct: 4.1, color: "var(--color-chart-4)", dash: "4.1 95.9", offset: -70.8 },
];

export const proposals = [
  {
    kind: "POLICY",
    title: "1銘柄上限を 15% → 20% に引き上げ",
    rationale:
      "6920 レーザーテックが出来高急増＋モメンタム上位で、現方針「短期は攻める」と整合。集中投資になるため「大損回避」とトレードオフ。現金20%維持を前提に上限を一時緩和する提案。レバレッジは使わない（ゼロカット許容）。",
  },
  {
    kind: "BUY",
    title: "6920 レーザーテック ・ 100 株",
    rationale:
      "出来高が20日平均の3.4倍、25日線を上抜け（momentum 0.88）。get_indicators / get_signals の事実に基づく。半導体は既に 18.2% を占め集中度が上がる点に注意。上の policy 承認が前提。承認しても発注はしない（記録のみ・約定後に手入力）。",
  },
];

export const policy = {
  rationale:
    "攻めるが退場はしない。資産が小さいうちは短期にリスクを取りリターンを狙う。ただし信用・レバレッジは使わない（個別の全損は受容するが借金は負わない）。全体の大損は現金バッファと1銘柄上限で避ける。",
  core: [
    { label: "リスク許容度", value: "高" },
    { label: "時間軸", value: "短〜中" },
    { label: "現金目標", value: "25%" },
    { label: "1銘柄上限", value: "15%" },
    { label: "目標リターン", value: "年+15%" },
    { label: "レバレッジ", value: "不可", warn: true },
  ],
  footer: "業種上限: 半導体 30% ／ 除外: なし ・ 最終更新 2026-05-28（チャット）",
};

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

export const journal = {
  meta: "2026-06-02 ・ openrouter",
  body: "全体は含み益 +6.9% で推移。半導体の強さが続く一方、1銘柄集中（最大18.2% / 上限15%）が方針の「大損回避」と擦れてきた。現金は20.3%で目標25%をやや下回る。今晩のシグナルは momentum 上位に半導体が集中し過熱の兆候。上限緩和を提案するなら現金25%維持を条件にしたい。レバレッジ不可は崩さない。",
};

// 資産推移スパークライン（mock の SVG path 座標をそのまま流用）。
export const trendPath =
  "M0,96 L60,90 L120,98 L180,76 L240,84 L300,60 L360,68 L420,48 L480,56 L540,38 L600,44 L660,24 L720,20";

export const nav = [
  {
    group: null,
    items: [
      { label: "Dashboard", icon: "▦", active: true },
      { label: "Stocks", icon: "≣" },
    ],
  },
  {
    group: "分析",
    items: [
      { label: "Signals", icon: "📈", phase: "P1" },
      { label: "Portfolio", icon: "⚖", phase: "P2" },
      { label: "Watchlist", icon: "👁", phase: "P4" },
    ],
  },
  {
    group: "Advisor",
    items: [
      { label: "Advisor", icon: "🧠", phase: "P3" },
      { label: "Policy", icon: "🧭", phase: "P3" },
      { label: "Journal", icon: "📓", phase: "P3" },
      { label: "Proposals", icon: "✓", phase: "P3" },
    ],
  },
  { group: "システム", items: [{ label: "Settings", icon: "⚙" }] },
];
