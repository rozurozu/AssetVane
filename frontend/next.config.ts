import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // 本番はイメージ・常駐メモリ縮小のため standalone output（ADR-021/022）。
  // frontend/Dockerfile の runner ステージが .next/standalone の server.js を Node 直起動する。
  output: "standalone",
};

export default nextConfig;
