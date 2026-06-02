import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // 本番はイメージ・常駐メモリ縮小のため standalone output（ADR-021/022）。今は dev 中心。
  // output: "standalone",
};

export default nextConfig;
