"use client";

import type { BacktestResult } from "@/lib/api";
import { ColorType, type IChartApi, LineSeries, type Time, createChart } from "lightweight-charts";
import { useEffect, useRef } from "react";

// 過去シミュレーション（buy&hold vs TOPIX）の累積カーブを 2 本線で描く（phase2-spec.md §4.4）。
// CandleChart.tsx と同じ作法: createChart → addSeries(LineSeries) → アンマウントで chart.remove()。
// 配色は DESIGN.md トークン実値（CandleChart と同値）。ポート=accent、ベンチ=inkMuted。

const COLORS = {
  canvas: "#090909",
  hairline: "#262626",
  inkMuted: "#9a9a9a",
  accent: "#0099ff",
} as const;

/** curve（{date, value}）を Lightweight Charts の LineData に変換（date は業務日文字列でよい）。 */
function toLineData(curve: BacktestResult["portfolio"]["curve"]) {
  return curve.map((p) => ({ time: p.date as Time, value: p.value }));
}

export function BacktestChart({ result }: { result: BacktestResult }) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const chart: IChartApi = createChart(el, {
      autoSize: true,
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

    const portfolio = chart.addSeries(LineSeries, {
      color: COLORS.accent,
      lineWidth: 2,
    });
    const benchmark = chart.addSeries(LineSeries, {
      color: COLORS.inkMuted,
      lineWidth: 1,
    });

    portfolio.setData(toLineData(result.portfolio.curve));
    benchmark.setData(toLineData(result.benchmark.curve));
    chart.timeScale().fitContent();

    return () => chart.remove();
  }, [result]);

  return <div ref={containerRef} className="h-[420px] w-full" />;
}
