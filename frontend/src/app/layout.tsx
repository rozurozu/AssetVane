import { AdvisorChat } from "@/components/advisor/AdvisorChat";
import { Sidebar } from "@/components/shell/Sidebar";
import { Topbar } from "@/components/shell/Topbar";
import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";

// Inter Variable をビルド時取得＝実行時 CDN 非依存（DESIGN.md）。CSS 変数で供給する。
const inter = Inter({ subsets: ["latin"], variable: "--font-inter", display: "swap" });

export const metadata: Metadata = {
  title: "AssetVane — Dashboard",
  description: "日米株を分析し AI と投資方針を相談する、単一ユーザー向け投資ダッシュボード。",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ja" className={inter.variable}>
      <body>
        {/* アプリシェル：サイドバー 220px ＋ 可変メイン（DESIGN.md / screens.md §1） */}
        <div className="grid min-h-screen grid-cols-[220px_1fr]">
          <Sidebar />
          <div className="flex min-w-0 flex-col">
            <Topbar />
            <main className="p-4">{children}</main>
          </div>
        </div>
        {/* 相談チャット（軸2）は全ページ常駐＝root layout に置く（ADR-024）。 */}
        <AdvisorChat />
      </body>
    </html>
  );
}
