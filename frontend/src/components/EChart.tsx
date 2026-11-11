// Обёртка ECharts с tree-shaking и автоматическим ресайзом.
import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { BarChart, LineChart } from "echarts/charts";
import { DataZoomComponent, GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([LineChart, BarChart, GridComponent, TooltipComponent, LegendComponent, DataZoomComponent, CanvasRenderer]);

export const C = {
  accent: "#2ee6a6",
  second: "#9aa3ad",
  third: "#5d8f7d",
  fourth: "#c9d1d9",
  grid: "#22262c",
  axis: "#5b616a",
  text: "#8b929c",
};

export function baseOption(): echarts.EChartsCoreOption {
  return {
    backgroundColor: "transparent",
    textStyle: { fontFamily: "system-ui, sans-serif", color: C.text },
    grid: { left: 44, right: 14, top: 30, bottom: 26, containLabel: false },
    legend: { top: 0, right: 0, textStyle: { color: C.text, fontSize: 11 }, itemWidth: 14, itemHeight: 8, icon: "roundRect" },
    tooltip: {
      trigger: "axis",
      backgroundColor: "#171a1f",
      borderColor: "#252a31",
      textStyle: { color: "#e6e8eb", fontSize: 12 },
      axisPointer: { lineStyle: { color: C.axis } },
    },
    xAxis: {
      type: "time",
      axisLine: { lineStyle: { color: C.grid } },
      axisLabel: { color: C.axis, fontSize: 10, hideOverlap: true },
      splitLine: { show: false },
    },
    yAxis: {
      type: "value",
      scale: true,
      axisLabel: { color: C.axis, fontSize: 10 },
      splitLine: { lineStyle: { color: C.grid } },
    },
  };
}

export function EChart({ option, className = "chart" }: { option: echarts.EChartsCoreOption; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    chart.current = echarts.init(ref.current, undefined, { renderer: "canvas" });
    const ro = new ResizeObserver(() => chart.current?.resize());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.current?.dispose();
      chart.current = null;
    };
  }, []);

  useEffect(() => {
    chart.current?.setOption(option, true);
  }, [option]);

  return <div ref={ref} className={className} />;
}
