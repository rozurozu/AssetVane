"use client";

import type { Quote } from "@/lib/api";
import {
  CandlestickSeries,
  ColorType,
  HistogramSeries,
  type IChartApi,
  type Time,
  createChart,
} from "lightweight-charts";
import { useEffect, useRef } from "react";

// TradingView Lightweight Charts v5 でローソク足＋出来高を描く（計画の Phase 0 完了条件の本体）。
// v5 では addCandlestickSeries() は廃止 → chart.addSeries(CandlestickSeries, ...)。
// クライアント専用ライブラリなので "use client"＋useRef/useEffect、アンマウントで chart.remove()。
// 配色は DESIGN.md トークンに合わせる（背景 canvas / 罫線 hairline / 陽線 up / 陰線 down / accent）。

// globals.css の @theme と同値（createChart は CSS 変数でなく実値を要求するため）。
const COLORS = {
  canvas: "#090909",
  hairline: "#262626",
  inkMuted: "#9a9a9a",
  up: "#22c55e",
  down: "#ef4444",
  accent: "#0099ff",
} as const;

export function CandleChart({ quotes }: { quotes: Quote[] }) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const chart: IChartApi = createChart(el, {
      autoSize: true, // 内蔵 ResizeObserver でコンテナ幅に追従
      layout: {
        background: { type: ColorType.Solid, color: COLORS.canvas },
        textColor: COLORS.inkMuted,
        fontSize: 11,
      },
      grid: {
        vertLines: { color: COLORS.hairline },
        horzLines: { color: COLORS.hairline },
      },
      rightPriceScale: { borderColor: COLORS.hairline },
      timeScale: { borderColor: COLORS.hairline },
      crosshair: { vertLine: { color: COLORS.accent }, horzLine: { color: COLORS.accent } },
    });

    const candle = chart.addSeries(CandlestickSeries, {
      upColor: COLORS.up,
      downColor: COLORS.down,
      borderVisible: false,
      wickUpColor: COLORS.up,
      wickDownColor: COLORS.down,
    });

    const volume = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "", // 価格軸とは別のオーバーレイ軸
    });
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });

    // 欠損のない足だけ描画。time は業務日文字列（YYYY-MM-DD）でよい。
    const valid = quotes.filter(
      (q) => q.open != null && q.high != null && q.low != null && q.close != null,
    );
    candle.setData(
      valid.map((q) => ({
        time: q.date as Time,
        open: q.open as number,
        high: q.high as number,
        low: q.low as number,
        close: q.close as number,
      })),
    );
    volume.setData(
      valid.map((q) => ({
        time: q.date as Time,
        value: q.volume ?? 0,
        color: (q.close as number) >= (q.open as number) ? "#1f4d33" : "#4d2222",
      })),
    );
    chart.timeScale().fitContent();

    return () => chart.remove();
  }, [quotes]);

  return <div ref={containerRef} className="h-[420px] w-full" />;
}
