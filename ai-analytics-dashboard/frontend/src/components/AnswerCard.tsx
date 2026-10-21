import { useState } from "react";
import { api } from "../api/client";
import type { AnswerData, ChartSpec } from "../api/types";
import ChartView from "./ChartView";
import DataTable from "./DataTable";
import HowPanel from "./HowPanel";

function PinButton({ chart }: { chart: ChartSpec }) {
  const [state, setState] = useState<"idle" | "busy" | "done" | "error">("idle");
  async function pin() {
    setState("busy");
    try {
      await api("/dashboard/pins", { body: { title: chart.title, chart } });
      setState("done");
    } catch {
      setState("error");
    }
  }
  return (
    <button type="button" className="btn-secondary px-2 py-1 text-xs" onClick={pin} disabled={state === "busy" || state === "done"}>
      {state === "done" ? "Pinned ✓" : state === "error" ? "Pin failed — retry" : "Pin to dashboard"}
    </button>
  );
}

export default function AnswerCard({ response }: { response: AnswerData }) {
  const tablesShownAsCharts = new Set(response.charts.filter((c) => c.type === "table").map((c) => c.title));
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        {response.cache_hit && <span className="badge bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-300">⚡ cached</span>}
        {!response.answerable && <span className="badge bg-slate-200 text-slate-700 dark:bg-slate-800 dark:text-slate-300">not answerable from data</span>}
      </div>
      <p className="whitespace-pre-wrap leading-relaxed">{response.answer}</p>
      {response.unverified_numbers.length > 0 && (
        <p className="rounded-lg bg-amber-50 p-2 text-sm text-amber-800 dark:bg-amber-950 dark:text-amber-300">
          ⚠ Some numbers in this answer could not be verified against the data: {response.unverified_numbers.join(", ")}. Check the tables below.
        </p>
      )}
      {response.charts.map((chart, i) => (
        <div key={`c${i}`} className="rounded-lg border border-slate-200 p-3 dark:border-slate-800">
          <div className="mb-2 flex items-center justify-between gap-2">
            <h3 className="font-medium">{chart.title}</h3>
            <PinButton chart={chart} />
          </div>
          <ChartView chart={chart} />
        </div>
      ))}
      {response.tables
        .filter((t) => !tablesShownAsCharts.has(t.title))
        .map((t) => (
          <div key={t.step_id + t.title}>
            <h3 className="mb-2 text-sm font-medium text-slate-600 dark:text-slate-300">{t.title}</h3>
            <DataTable columns={t.columns} rows={t.rows} totalRows={t.row_count} filename={`${t.step_id}.csv`} />
          </div>
        ))}
      <HowPanel response={response} />
    </div>
  );
}
