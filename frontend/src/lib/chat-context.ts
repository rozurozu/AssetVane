// route（usePathname）→ 画面コンテキスト（ChatContext）の写像（ADR-025・screens.md §5）。
// 「ページ＋主対象（focus）」の軽量ヒントのみ。数値・画面データは絶対に載せない（spec §9.1）。
// AI は数値が要れば該当 Tool で取り直す前提。

import type { ChatContext } from "@/lib/api";

/** pathname を ChatContext（page＋focus?）へ変換する。
 * /stocks/[code] → {page:"stock_detail", focus:{type:"stock", code}}
 * /portfolio→portfolio, /signals→signals, /policy→policy,
 * /proposals→proposals, /journal→journal, /→dashboard。その他は page だけ。 */
export function pathnameToContext(pathname: string): ChatContext {
  // 銘柄詳細は code を focus に載せる（数値ではないので OK）。
  const stockDetail = pathname.match(/^\/stocks\/([^/]+)\/?$/);
  if (stockDetail) {
    return {
      page: "stock_detail",
      focus: { type: "stock", code: decodeURIComponent(stockDetail[1]) },
    };
  }

  if (pathname === "/") return { page: "dashboard" };
  if (pathname === "/stocks" || pathname.startsWith("/stocks/")) return { page: "stocks" };
  if (pathname.startsWith("/portfolio")) return { page: "portfolio" };
  if (pathname.startsWith("/transactions")) return { page: "transactions" };
  if (pathname.startsWith("/signals")) return { page: "signals" };
  if (pathname.startsWith("/policy")) return { page: "policy" };
  if (pathname.startsWith("/proposals")) return { page: "proposals" };
  if (pathname.startsWith("/journal")) return { page: "journal" };

  // 未知ルートはページ名だけ（先頭スラッシュを落とす・空なら dashboard）。
  const slug = pathname.replace(/^\//, "").split("/")[0];
  return { page: slug || "dashboard" };
}

/** context を 1 行の日本語ラベルにする（チャット上部のヒント表示用）。数値は出さない。 */
export function contextLabel(ctx: ChatContext): string {
  const labels: Record<string, string> = {
    dashboard: "Dashboard",
    stocks: "銘柄一覧",
    stock_detail: "銘柄詳細",
    portfolio: "Portfolio",
    transactions: "取引入力",
    signals: "Signals",
    policy: "Policy",
    proposals: "Proposals",
    journal: "Journal",
  };
  const base = labels[ctx.page] ?? ctx.page;
  if (ctx.focus?.code) return `${base}（${ctx.focus.code}）`;
  if (ctx.focus?.id != null) return `${base}（#${ctx.focus.id}）`;
  return base;
}
