import type { ChartSpec } from "../api/types";

type Option = Record<string, unknown>;

const unique = (values: string[]) => Array.from(new Set(values));
const num = (v: unknown) => (v === null || v === undefined || v === "" ? null : Number(v));

export function buildOption(chart: ChartSpec): Option | null {
  if (chart.type === "table" || chart.type === "kpi") return null;
  const { columns, rows } = chart.data;
  const idx = (name: string | null) => (name ? columns.indexOf(name) : -1);
  const xi = idx(chart.x);
  const yIdx = chart.y.map(idx);
  if (xi < 0 || yIdx.length === 0 || yIdx.some((i) => i < 0)) return null;

  const base: Option = {
    backgroundColor: "transparent",
    tooltip: { trigger: chart.type === "pie" || chart.type === "scatter" ? "item" : "axis" },
    grid: { left: 64, right: 24, top: 40, bottom: 56, containLabel: true },
  };

  if (chart.type === "pie") {
    return {
      ...base,
      legend: { type: "scroll", bottom: 0 },
      series: [{ type: "pie", radius: ["35%", "65%"], data: rows.map((r) => ({ name: String(r[xi]), value: num(r[yIdx[0]]) })) }],
    };
  }
  if (chart.type === "scatter") {
    return {
      ...base,
      xAxis: { type: "value", name: chart.x ?? "" },
      yAxis: { type: "value", name: chart.y[0] },
      series: [{ type: "scatter", data: rows.map((r) => [num(r[xi]), num(r[yIdx[0]])]) }],
    };
  }

  const seriesType = chart.type === "bar" ? "bar" : "line";
  const extra = (): Option => ({
    ...(chart.type === "area" ? { areaStyle: {} } : {}),
    ...(seriesType === "line" ? { smooth: true, showSymbol: rows.length <= 40 } : {}),
  });
  const si = idx(chart.series);
  let categories: string[];
  let series: Option[];
  if (si >= 0) {
    categories = unique(rows.map((r) => String(r[xi])));
    series = unique(rows.map((r) => String(r[si]))).map((group) => {
      const values = new Map<string, number | null>();
      rows.filter((r) => String(r[si]) === group).forEach((r) => values.set(String(r[xi]), num(r[yIdx[0]])));
      return { name: group, type: seriesType, data: categories.map((c) => values.get(c) ?? null), ...extra() };
    });
  } else {
    categories = rows.map((r) => String(r[xi]));
    series = yIdx.map((i) => ({ name: columns[i], type: seriesType, data: rows.map((r) => num(r[i])), ...extra() }));
  }
  return {
    ...base,
    ...(series.length > 1 ? { legend: { type: "scroll", top: 0 } } : {}),
    xAxis: { type: "category", data: categories, axisLabel: { rotate: categories.length > 8 ? 30 : 0 } },
    yAxis: { type: "value" },
    series,
  };
}
