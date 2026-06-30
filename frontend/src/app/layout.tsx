import { AdvisorChat } from "@/components/advisor/AdvisorChat";
import { Sidebar } from "@/components/shell/Sidebar";
import { Topbar } from "@/components/shell/Topbar";
import { AdvisorChatProvider } from "@/lib/advisor-chat-context";
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
        {/* 相談チャット（軸2）の会話状態を全ページ＋専用ページ /advisor で共有（ADR-024/065）。
            Provider をシェルごと包むことで、フローティングと /advisor が同一会話を見る。 */}
        <AdvisorChatProvider>
          {/* アプリシェル：サイドバー 220px ＋ 可変メイン（DESIGN.md / screens.md §1） */}
          <div className="grid min-h-screen grid-cols-[220px_1fr]">
            <Sidebar />
            <div className="flex min-w-0 flex-col">
              <Topbar />
              <main className="p-4">{children}</main>
            </div>
          </div>
          {/* 相談チャット（軸2）の常駐フローティング枠＝root layout に置く（ADR-024）。 */}
          <AdvisorChat />
        </AdvisorChatProvider>
      </body>
    </html>
  );
}
