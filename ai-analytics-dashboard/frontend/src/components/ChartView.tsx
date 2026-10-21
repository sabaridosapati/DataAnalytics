import ReactECharts from "echarts-for-react";
import type { ChartSpec } from "../api/types";
import { buildOption } from "../charts/buildOption";
import { useIsDark } from "../utils/theme";
import DataTable from "./DataTable";

export default function ChartView({ chart, height = 320 }: { chart: ChartSpec; height?: number }) {
  const dark = useIsDark();
  if (chart.type === "kpi") {
    return (
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        {chart.data.rows.map((r, i) => (
          <div key={i} className="rounded-lg border border-slate-200 p-3 dark:border-slate-800">
            <div className="text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">{String(r[0])}</div>
            <div className="mt-1 text-2xl font-semibold tabular-nums">
              {typeof r[1] === "number" ? r[1].toLocaleString(undefined, { maximumFractionDigits: 2 }) : String(r[1] ?? "—")}
            </div>
          </div>
        ))}
      </div>
    );
  }
  const option = buildOption(chart);
  if (!option) return <DataTable columns={chart.data.columns} rows={chart.data.rows} />;
  return <ReactECharts option={option} theme={dark ? "dark" : undefined} style={{ height }} notMerge lazyUpdate />;
}
