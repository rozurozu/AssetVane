import type { NextConfig } from "next";

// backend の置き場所。ブラウザではなく Next サーバ（この rewrites プロキシ）から到達する先なので、
// dev/prod とも Docker 内部 DNS の固定名 `backend:8000` でよい（ホスト非依存＝ADR-037）。
// compose 無しのホスト直 dev（cd frontend && npm run dev）だけ localhost:8000 にフォールバックする。
const backendOrigin = process.env.BACKEND_ORIGIN ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  // 本番はイメージ・常駐メモリ縮小のため standalone output（ADR-021/022）。
  // frontend/Dockerfile の runner ステージが .next/standalone の server.js を Node 直起動する。
  output: "standalone",

  experimental: {
    // 下の rewrites プロキシの「上流（backend）応答待ち」上限（ms）。Next は既定で 30s に切る
    // （next 内部 proxy-request.js: `proxyTimeout || 30_000`）。AI Advisor の `POST /chat` は
    // クラウド強モデル推論＋Tool ループの往復で 30s を普通に超える。30s で切られると上流
    // backend:8000 への socket が ECONNRESET（"socket hang up"）され、backend が 200 を返して
    // いてもブラウザには素の HTTP 500（"Internal Server Error"）が返り「Advisor に繋がらなかった」
    // になる。backend の LLM タイムアウト（llm_timeout_seconds・リトライ込み）より余裕を持たせ、
    // 長い相談ターンを切らない（これより遅いときは backend 自身が 502 で返す）。
    proxyTimeout: 200_000,
  },

  // 同一オリジン化プロキシ（ADR-037）。ブラウザは常に自分のオリジンの `/api/*` だけを叩き、
  // Next サーバが裏で backend へ素通しする。これで CORS と API_URL 焼き込みが両方不要になる
  // （ブラウザが backend のホストを知る必要が無くなる＝Pi の IP が変わっても無設定で動く）。
  // 透過 HTTP プロキシなので Next は DB を触らず REST を素通しするだけ＝ADR-005 を侵さない。
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backendOrigin}/:path*` }];
  },
};

export default nextConfig;
