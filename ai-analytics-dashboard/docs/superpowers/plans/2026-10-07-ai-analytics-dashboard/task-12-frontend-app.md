# Task 12: Frontend app — layout, Ask, Datasets, Dashboard, Admin, charts, Docker image

**Files:**
- Create: `frontend/src/main.tsx`, `frontend/src/App.tsx`
- Create: `frontend/src/charts/buildOption.ts`
- Create: `frontend/src/components/Layout.tsx`, `ChartView.tsx`, `DataTable.tsx`, `AnswerCard.tsx`, `HowPanel.tsx`, `StatusBadge.tsx`
- Create: `frontend/src/pages/Ask.tsx`, `Datasets.tsx`, `Dashboard.tsx`, `Admin.tsx`
- Create: `frontend/Dockerfile`, `frontend/nginx.conf`
- Test: `frontend/src/charts/buildOption.test.ts`, `frontend/src/components/DataTable.test.tsx`

**Interfaces:**
- Consumes: Task 11 (`api`, `useAuth`, types, `useIsDark`, `toggleTheme`, `initTheme`, `downloadCsv`); backend endpoints from Tasks 7 and 10.
- Produces `buildOption(chart: ChartSpec): Record<string, unknown> | null` — `null` for `table`, `kpi`, or invalid fields.
- Example questions on the Ask page = `DEMO_QUESTIONS` from Task 10 (verbatim).

- [ ] **Step 1: Write the failing tests**

**File: `frontend/src/charts/buildOption.test.ts`**
```ts
import type { ChartSpec } from "../api/types";
import { buildOption } from "./buildOption";

const data = { columns: ["region", "total"], rows: [["East", 10], ["West", 20]] };
const spec = (over: Partial<ChartSpec>): ChartSpec => ({ type: "bar", title: "t", x: "region", y: ["total"], series: null, data, ...over });

describe("buildOption", () => {
  it("builds a single-series bar chart", () => {
    const o = buildOption(spec({})) as any;
    expect(o.xAxis.data).toEqual(["East", "West"]);
    expect(o.series).toHaveLength(1);
    expect(o.series[0]).toMatchObject({ type: "bar", name: "total", data: [10, 20] });
  });

  it("pivots rows into one series per category", () => {
    const rows = [["Jan", "East", 1], ["Jan", "West", 2], ["Feb", "East", 3]];
    const o = buildOption(spec({ type: "line", x: "month", y: ["v"], series: "region", data: { columns: ["month", "region", "v"], rows } })) as any;
    expect(o.xAxis.data).toEqual(["Jan", "Feb"]);
    expect(o.series.map((s: any) => [s.name, s.data])).toEqual([["East", [1, 3]], ["West", [2, null]]]);
    expect(o.legend).toBeDefined();
  });

  it("builds pie and area charts", () => {
    const pie = buildOption(spec({ type: "pie" })) as any;
    expect(pie.series[0].data).toEqual([{ name: "East", value: 10 }, { name: "West", value: 20 }]);
    const area = buildOption(spec({ type: "area" })) as any;
    expect(area.series[0].type).toBe("line");
    expect(area.series[0].areaStyle).toEqual({});
  });

  it("returns null for tables, KPIs and unknown columns", () => {
    expect(buildOption(spec({ type: "table" }))).toBeNull();
    expect(buildOption(spec({ type: "kpi" }))).toBeNull();
    expect(buildOption(spec({ x: "nope" }))).toBeNull();
    expect(buildOption(spec({ y: ["nope"] }))).toBeNull();
  });
});
```

**File: `frontend/src/components/DataTable.test.tsx`**
```tsx
import { fireEvent, render, screen, within } from "@testing-library/react";
import DataTable from "./DataTable";

describe("DataTable", () => {
  it("renders rows and sorts by a column when its header is clicked", () => {
    render(<DataTable columns={["region", "total"]} rows={[["East", 10], ["West", 30], ["North", 20]]} />);
    const firstCell = () => within(screen.getAllByRole("row")[1]).getAllByRole("cell")[0].textContent;
    expect(firstCell()).toBe("East");
    fireEvent.click(screen.getByRole("button", { name: /total/ }));
    expect(firstCell()).toBe("East");
    fireEvent.click(screen.getByRole("button", { name: /total/ }));
    expect(firstCell()).toBe("West");
  });

  it("shows an empty state", () => {
    render(<DataTable columns={["a"]} rows={[]} />);
    expect(screen.getByText(/no rows/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `NODE> npx vitest run`
Expected: FAIL — cannot resolve `./buildOption` / `./DataTable`.

- [ ] **Step 3: Implement chart option builder and presentational components**

**File: `frontend/src/charts/buildOption.ts`**
```ts
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
```

**File: `frontend/src/components/DataTable.tsx`**
```tsx
import { useMemo, useState } from "react";
import { downloadCsv } from "../utils/csv";

const PAGE = 200;

function format(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") return Number.isInteger(v) ? v.toLocaleString() : v.toLocaleString(undefined, { maximumFractionDigits: 4 });
  return String(v);
}

function compare(a: unknown, b: unknown): number {
  if (a === b) return 0;
  if (a === null || a === undefined) return 1;
  if (b === null || b === undefined) return -1;
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b), undefined, { numeric: true });
}

interface Props {
  columns: string[];
  rows: unknown[][];
  filename?: string;
  totalRows?: number;
}

export default function DataTable({ columns, rows, filename, totalRows }: Props) {
  const [sort, setSort] = useState<{ col: number; dir: 1 | -1 } | null>(null);
  const sorted = useMemo(() => {
    if (!sort) return rows;
    return [...rows].sort((a, b) => compare(a[sort.col], b[sort.col]) * sort.dir);
  }, [rows, sort]);

  function toggle(col: number) {
    setSort((s) => (s?.col === col ? { col, dir: s.dir === 1 ? -1 : 1 } : { col, dir: 1 }));
  }

  if (rows.length === 0) {
    return <p className="py-4 text-sm text-slate-500 dark:text-slate-400">No rows returned.</p>;
  }
  return (
    <div>
      <div className="max-h-96 overflow-auto rounded-lg border border-slate-200 dark:border-slate-800">
        <table className="min-w-full text-sm">
          <thead className="sticky top-0 bg-slate-100 dark:bg-slate-800">
            <tr>
              {columns.map((c, i) => (
                <th key={c + i} className="whitespace-nowrap px-3 py-2 text-left font-medium">
                  <button type="button" onClick={() => toggle(i)} className="inline-flex items-center gap-1 hover:underline">
                    {c}
                    <span aria-hidden className="text-xs text-slate-400">{sort?.col === i ? (sort.dir === 1 ? "▲" : "▼") : ""}</span>
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.slice(0, PAGE).map((r, ri) => (
              <tr key={ri} className="border-t border-slate-100 dark:border-slate-800">
                {r.map((v, ci) => (
                  <td key={ci} className={`whitespace-nowrap px-3 py-1.5 ${typeof v === "number" ? "text-right tabular-nums" : ""}`}>{format(v)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-2 flex items-center justify-between text-xs text-slate-500 dark:text-slate-400">
        <span>
          Showing {Math.min(PAGE, rows.length).toLocaleString()} of {(totalRows ?? rows.length).toLocaleString()} rows
        </span>
        {filename && (
          <button type="button" className="btn-secondary px-2 py-1 text-xs" onClick={() => downloadCsv(filename, columns, sorted)}>
            Download CSV
          </button>
        )}
      </div>
    </div>
  );
}
```

**File: `frontend/src/components/ChartView.tsx`**
```tsx
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
```

**File: `frontend/src/components/StatusBadge.tsx`**
```tsx
const STYLES: Record<string, string> = {
  ready: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  ok: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  processing: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  pending: "bg-slate-200 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
  partial: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  skipped: "bg-slate-200 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
  unanswerable: "bg-slate-200 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
};

export default function StatusBadge({ status }: { status: string }) {
  const style = STYLES[status] ?? "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300";
  return <span className={`badge ${style}`}>{status}</span>;
}
```

**File: `frontend/src/components/HowPanel.tsx`**
```tsx
import type { QueryResponse } from "../api/types";
import StatusBadge from "./StatusBadge";

export default function HowPanel({ response }: { response: QueryResponse }) {
  if (!response.plan) return null;
  return (
    <details className="mt-3 rounded-lg border border-slate-200 p-3 text-sm dark:border-slate-800">
      <summary className="cursor-pointer select-none font-medium">How I got this</summary>
      <div className="mt-3 space-y-3">
        <div>
          <div className="mb-1 text-xs uppercase tracking-wide text-slate-500">Plan ({response.plan.intent})</div>
          <ol className="space-y-1">
            {response.steps.map((s) => (
              <li key={s.id} className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-xs text-slate-500">{s.id}</span>
                <span className="badge bg-indigo-100 text-indigo-800 dark:bg-indigo-950 dark:text-indigo-300">{s.agent}</span>
                <span>{s.task}</span>
                <StatusBadge status={s.status} />
                <span className="text-xs text-slate-500">{s.ms} ms{s.cache_hit ? " · cached" : ""}</span>
                {s.error && <span className="w-full text-xs text-red-600 dark:text-red-400">{s.error}</span>}
              </li>
            ))}
          </ol>
        </div>
        {response.sql.length > 0 && (
          <div>
            <div className="mb-1 text-xs uppercase tracking-wide text-slate-500">Validated SQL (read-only)</div>
            {response.sql.map((q, i) => (
              <pre key={i} className="overflow-x-auto rounded bg-slate-100 p-2 text-xs dark:bg-slate-800">{q}</pre>
            ))}
          </div>
        )}
        {response.sources.length > 0 && (
          <div>
            <div className="mb-1 text-xs uppercase tracking-wide text-slate-500">Document sources</div>
            <ul className="space-y-2">
              {response.sources.map((s) => (
                <li key={s.chunk_id}>
                  <span className="font-medium">{s.source}</span>
                  {s.page != null && <span className="text-slate-500"> · page {s.page}</span>}
                  <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">“{s.snippet}…”</p>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </details>
  );
}
```

**File: `frontend/src/components/AnswerCard.tsx`**
```tsx
import { useState } from "react";
import { api } from "../api/client";
import type { ChartSpec, QueryResponse } from "../api/types";
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

export default function AnswerCard({ response }: { response: QueryResponse }) {
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `NODE> npx vitest run`
Expected: all pass.

- [ ] **Step 5: Implement layout, pages, routing**

**File: `frontend/src/components/Layout.tsx`**
```tsx
import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { toggleTheme, useIsDark } from "../utils/theme";

const linkClass = ({ isActive }: { isActive: boolean }) =>
  `block rounded-lg px-3 py-2 text-sm font-medium ${
    isActive ? "bg-indigo-600 text-white" : "text-slate-700 hover:bg-slate-200 dark:text-slate-300 dark:hover:bg-slate-800"
  }`;

export default function Layout() {
  const { user, logout } = useAuth();
  const dark = useIsDark();
  return (
    <div className="flex min-h-screen flex-col md:flex-row">
      <aside className="flex shrink-0 flex-col gap-1 border-b border-slate-200 bg-white p-4 md:w-60 md:border-b-0 md:border-r dark:border-slate-800 dark:bg-slate-900">
        <div className="mb-4 flex items-center gap-2">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-indigo-600 text-sm font-bold text-white">AI</div>
          <span className="font-semibold">AI Analytics</span>
        </div>
        <nav className="flex gap-1 overflow-x-auto md:flex-col">
          <NavLink to="/" end className={linkClass}>Ask</NavLink>
          <NavLink to="/datasets" className={linkClass}>Datasets</NavLink>
          <NavLink to="/dashboard" className={linkClass}>Dashboard</NavLink>
          {user?.role === "admin" && <NavLink to="/admin" className={linkClass}>Admin</NavLink>}
        </nav>
        <div className="mt-auto hidden space-y-2 pt-6 text-sm md:block">
          <div className="truncate text-slate-500 dark:text-slate-400" title={user?.email}>{user?.username ?? user?.email}</div>
          <div className="flex gap-2">
            <button type="button" className="btn-secondary flex-1 px-2 py-1 text-xs" onClick={() => toggleTheme()}>{dark ? "Light" : "Dark"} mode</button>
            <button type="button" className="btn-secondary flex-1 px-2 py-1 text-xs" onClick={logout}>Sign out</button>
          </div>
        </div>
      </aside>
      <main className="min-w-0 flex-1 p-4 md:p-8">
        <div className="mb-4 flex justify-end gap-2 md:hidden">
          <button type="button" className="btn-secondary px-2 py-1 text-xs" onClick={() => toggleTheme()}>{dark ? "Light" : "Dark"}</button>
          <button type="button" className="btn-secondary px-2 py-1 text-xs" onClick={logout}>Sign out</button>
        </div>
        <Outlet />
      </main>
    </div>
  );
}
```

**File: `frontend/src/pages/Ask.tsx`**
```tsx
import { useEffect, useRef, useState, type FormEvent } from "react";
import { api } from "../api/client";
import type { QueryResponse } from "../api/types";
import AnswerCard from "../components/AnswerCard";

const EXAMPLES = [
  "What is the total revenue by region?",
  "Show the monthly revenue trend",
  "Why did Q3 performance drop?",
  "What is the average salary by department?",
  "Show the monthly revenue trend and explain the Q3 dip",
];

interface Turn {
  id: number;
  question: string;
  response?: QueryResponse;
  error?: string;
  loading: boolean;
}

export default function Ask() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [question, setQuestion] = useState("");
  const bottom = useRef<HTMLDivElement>(null);
  const busy = turns.some((t) => t.loading);

  useEffect(() => {
    bottom.current?.scrollIntoView?.({ behavior: "smooth" });
  }, [turns]);

  async function ask(text: string) {
    const q = text.trim();
    if (!q || busy) return;
    const id = Date.now();
    const history = turns
      .filter((t) => t.response?.answerable)
      .slice(-3)
      .map((t) => ({ question: t.question, answer: t.response!.answer.slice(0, 4000) }));
    setTurns((ts) => [...ts, { id, question: q, loading: true }]);
    setQuestion("");
    try {
      const response = await api<QueryResponse>("/query", { body: { question: q, history } });
      setTurns((ts) => ts.map((t) => (t.id === id ? { ...t, response, loading: false } : t)));
    } catch (err) {
      setTurns((ts) => ts.map((t) => (t.id === id ? { ...t, error: err instanceof Error ? err.message : "Request failed", loading: false } : t)));
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    void ask(question);
  }

  return (
    <div className="mx-auto max-w-4xl">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-2xl font-semibold">Ask your data</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">Questions are routed to SQL, document, compute and chart agents. Every number is checked against the data.</p>
        </div>
        {turns.length > 0 && (
          <button type="button" className="btn-secondary" onClick={() => setTurns([])} disabled={busy}>New conversation</button>
        )}
      </div>

      {turns.length === 0 && (
        <div className="card mb-6">
          <p className="mb-3 text-sm font-medium">Try one of these:</p>
          <div className="flex flex-wrap gap-2">
            {EXAMPLES.map((e) => (
              <button key={e} type="button" className="btn-secondary text-left" onClick={() => void ask(e)}>{e}</button>
            ))}
          </div>
        </div>
      )}

      <div className="space-y-6">
        {turns.map((t) => (
          <div key={t.id} className="space-y-3">
            <div className="flex justify-end">
              <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-indigo-600 px-4 py-2 text-white">{t.question}</div>
            </div>
            <div className="card">
              {t.loading && <p className="animate-pulse text-sm text-slate-500">Analyzing… planning steps, querying data and checking numbers.</p>}
              {t.error && <p className="error-text" role="alert">{t.error}</p>}
              {t.response && <AnswerCard response={t.response} />}
            </div>
          </div>
        ))}
        <div ref={bottom} />
      </div>

      <form onSubmit={onSubmit} className="sticky bottom-0 mt-6 flex gap-2 bg-slate-50 py-4 dark:bg-slate-950">
        <label htmlFor="question" className="sr-only">Question</label>
        <input
          id="question"
          className="input"
          placeholder="e.g. Which product category grew fastest in Q4?"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          maxLength={2000}
        />
        <button type="submit" className="btn shrink-0" disabled={busy || !question.trim()}>Ask</button>
      </form>
    </div>
  );
}
```

**File: `frontend/src/pages/Datasets.tsx`**
```tsx
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState, type DragEvent } from "react";
import { api } from "../api/client";
import type { Dataset, DatasetDetail } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import DataTable from "../components/DataTable";
import StatusBadge from "../components/StatusBadge";

const ACCEPT = ".csv,.tsv,.psv,.dat,.txt,.md,.log,.json,.jsonl,.ndjson,.xlsx,.xls,.pdf,.docx";

function Detail({ id }: { id: number }) {
  const { data, isLoading, error } = useQuery({ queryKey: ["dataset", id], queryFn: () => api<DatasetDetail>(`/datasets/${id}`) });
  if (isLoading) return <p className="text-sm text-slate-500">Loading…</p>;
  if (error || !data) return <p className="error-text">Could not load dataset.</p>;
  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold">{data.name}</h2>
        <p className="text-sm text-slate-500 dark:text-slate-400">
          Query name <code className="rounded bg-slate-100 px-1 dark:bg-slate-800">{data.slug}</code> · {data.kind}
          {data.kind === "table" ? ` · ${data.row_count.toLocaleString()} rows` : ` · ${data.chunk_count} chunks`}
        </p>
      </div>
      {data.columns.length > 0 && (
        <div>
          <h3 className="mb-2 text-sm font-medium">Columns</h3>
          <DataTable
            columns={["column", "type", "description", "examples"]}
            rows={data.columns.map((c) => [c.column_name, c.pg_type, c.description || "—", c.sample_values.slice(0, 3).join(", ")])}
          />
        </div>
      )}
      {data.preview.rows && (
        <div>
          <h3 className="mb-2 text-sm font-medium">First rows</h3>
          <DataTable columns={data.preview.columns ?? []} rows={data.preview.rows} />
        </div>
      )}
      {data.preview.chunks && (
        <div>
          <h3 className="mb-2 text-sm font-medium">Text chunks</h3>
          <ul className="space-y-2">
            {data.preview.chunks.map((c) => (
              <li key={c.index} className="rounded-lg border border-slate-200 p-2 text-xs dark:border-slate-800">
                <div className="mb-1 text-slate-500">#{c.index}{c.metadata.page != null ? ` · page ${String(c.metadata.page)}` : ""}</div>
                <p className="line-clamp-4 whitespace-pre-wrap">{c.content}</p>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export default function Datasets() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  const { data: datasets = [], isLoading } = useQuery({
    queryKey: ["datasets"],
    queryFn: () => api<Dataset[]>("/datasets"),
    refetchInterval: (query) => ((query.state.data ?? []).some((d) => d.status === "pending" || d.status === "processing") ? 1500 : false),
  });

  async function upload(files: FileList | File[]) {
    const list = Array.from(files);
    if (list.length === 0) return;
    const form = new FormData();
    list.forEach((f) => form.append("files", f));
    setUploading(true);
    setMessage(null);
    try {
      await api<Dataset[]>("/datasets/upload", { form });
      setMessage({ kind: "ok", text: `Uploaded ${list.length} file(s). Processing…` });
      await qc.invalidateQueries({ queryKey: ["datasets"] });
    } catch (err) {
      setMessage({ kind: "error", text: err instanceof Error ? err.message : "Upload failed." });
    } finally {
      setUploading(false);
      if (input.current) input.current.value = "";
    }
  }

  async function remove(d: Dataset) {
    if (!window.confirm(`Delete "${d.name}" (${d.slug})? This cannot be undone.`)) return;
    try {
      await api(`/datasets/${d.id}`, { method: "DELETE" });
      if (selected === d.id) setSelected(null);
      await qc.invalidateQueries({ queryKey: ["datasets"] });
    } catch (err) {
      setMessage({ kind: "error", text: err instanceof Error ? err.message : "Delete failed." });
    }
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDragging(false);
    void upload(e.dataTransfer.files);
  }

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Datasets</h1>
        <p className="text-sm text-slate-500 dark:text-slate-400">Tables go to PostgreSQL; text and PDFs are chunked and embedded into pgvector.</p>
      </div>

      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={`card flex flex-col items-center justify-center gap-3 border-2 border-dashed py-10 text-center ${dragging ? "border-indigo-500 bg-indigo-50 dark:bg-indigo-950" : ""}`}
      >
        <p className="font-medium">Drag & drop files here</p>
        <p className="text-xs text-slate-500 dark:text-slate-400">CSV, TSV, delimited .dat/.txt, JSON/JSONL, Excel, PDF, Word, text — up to 50 MB each, 10 per upload</p>
        <input ref={input} type="file" multiple accept={ACCEPT} className="hidden" id="file-input" onChange={(e) => e.target.files && void upload(e.target.files)} />
        <label htmlFor="file-input" className="btn cursor-pointer">{uploading ? "Uploading…" : "Choose files"}</label>
        {message && <p className={message.kind === "ok" ? "text-sm text-emerald-600 dark:text-emerald-400" : "error-text"}>{message.text}</p>}
      </div>

      <div className="grid gap-6 lg:grid-cols-5">
        <div className="card lg:col-span-2">
          {isLoading && <p className="text-sm text-slate-500">Loading…</p>}
          {!isLoading && datasets.length === 0 && <p className="text-sm text-slate-500">No datasets yet. Upload the sample files from <code>backend/tests/fixtures</code> to try the demo.</p>}
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {datasets.map((d) => (
              <li key={d.id} className={`flex items-start gap-2 py-2 ${selected === d.id ? "bg-slate-50 dark:bg-slate-800/50" : ""}`}>
                <button type="button" className="min-w-0 flex-1 text-left" onClick={() => setSelected(d.id)} disabled={d.status !== "ready"}>
                  <div className="truncate text-sm font-medium">{d.name}</div>
                  <div className="flex items-center gap-2 text-xs text-slate-500">
                    <StatusBadge status={d.status} />
                    <span>{d.kind === "pending" ? d.file_type : d.kind}</span>
                    {d.status === "ready" && <code>{d.slug}</code>}
                  </div>
                  {d.error && <div className="mt-1 text-xs text-red-600 dark:text-red-400">{d.error}</div>}
                </button>
                {user?.role === "admin" && d.status !== "pending" && d.status !== "processing" && (
                  <button type="button" className="btn-danger px-2 py-1 text-xs" onClick={() => void remove(d)}>Delete</button>
                )}
              </li>
            ))}
          </ul>
        </div>
        <div className="card lg:col-span-3">
          {selected ? <Detail id={selected} /> : <p className="text-sm text-slate-500">Select a ready dataset to see its schema and preview.</p>}
        </div>
      </div>
    </div>
  );
}
```

**File: `frontend/src/pages/Dashboard.tsx`**
```tsx
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { Pin } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import ChartView from "../components/ChartView";

export default function Dashboard() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const [busy, setBusy] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { data: pins = [], isLoading } = useQuery({ queryKey: ["pins"], queryFn: () => api<Pin[]>("/dashboard/pins") });

  async function act(pin: Pin, action: "refresh" | "delete") {
    setBusy(pin.id);
    setError(null);
    try {
      if (action === "refresh") await api(`/dashboard/pins/${pin.id}/refresh`, { method: "POST" });
      else await api(`/dashboard/pins/${pin.id}`, { method: "DELETE" });
      await qc.invalidateQueries({ queryKey: ["pins"] });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Action failed.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Dashboard</h1>
        <p className="text-sm text-slate-500 dark:text-slate-400">Charts pinned by anyone in your organization. Refresh re-runs the validated query — no LLM involved.</p>
      </div>
      {error && <p className="error-text" role="alert">{error}</p>}
      {isLoading && <p className="text-sm text-slate-500">Loading…</p>}
      {!isLoading && pins.length === 0 && <div className="card text-sm text-slate-500">Nothing pinned yet. Ask a question and use “Pin to dashboard” on a chart.</div>}
      <div className="grid gap-6 md:grid-cols-2">
        {pins.map((pin) => (
          <div key={pin.id} className="card">
            <div className="mb-2 flex items-start justify-between gap-2">
              <div>
                <h2 className="font-medium">{pin.title}</h2>
                <p className="text-xs text-slate-500">{new Date(pin.created_at).toLocaleString()}</p>
              </div>
              <div className="flex gap-2">
                {pin.sql && (
                  <button type="button" className="btn-secondary px-2 py-1 text-xs" disabled={busy === pin.id} onClick={() => void act(pin, "refresh")}>Refresh</button>
                )}
                {(user?.role === "admin" || user?.id === pin.created_by) && (
                  <button type="button" className="btn-danger px-2 py-1 text-xs" disabled={busy === pin.id} onClick={() => void act(pin, "delete")}>Remove</button>
                )}
              </div>
            </div>
            <ChartView chart={pin.chart} height={280} />
          </div>
        ))}
      </div>
    </div>
  );
}
```

**File: `frontend/src/pages/Admin.tsx`**
```tsx
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { AdminUser, AuditItem } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import StatusBadge from "../components/StatusBadge";

const PAGE = 25;

export default function Admin() {
  const { user: me } = useAuth();
  const qc = useQueryClient();
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const users = useQuery({ queryKey: ["admin-users"], queryFn: () => api<AdminUser[]>("/admin/users") });
  const audit = useQuery({
    queryKey: ["audit", offset],
    queryFn: () => api<{ items: AuditItem[]; total: number }>(`/admin/audit?limit=${PAGE}&offset=${offset}`),
  });

  async function patch(u: AdminUser, body: Partial<Pick<AdminUser, "is_active" | "role">>) {
    setError(null);
    try {
      await api(`/admin/users/${u.id}`, { method: "PATCH", body });
      await qc.invalidateQueries({ queryKey: ["admin-users"] });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Update failed.");
    }
  }

  const total = audit.data?.total ?? 0;
  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <h1 className="text-2xl font-semibold">Admin</h1>
      {error && <p className="error-text" role="alert">{error}</p>}

      <section className="card">
        <h2 className="mb-3 font-medium">Users</h2>
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead className="text-left text-xs uppercase text-slate-500">
              <tr><th className="py-2 pr-4">Email</th><th className="pr-4">Role</th><th className="pr-4">Verified</th><th className="pr-4">Status</th><th /></tr>
            </thead>
            <tbody>
              {(users.data ?? []).map((u) => (
                <tr key={u.id} className="border-t border-slate-100 dark:border-slate-800">
                  <td className="py-2 pr-4">{u.email}{u.username ? ` (${u.username})` : ""}</td>
                  <td className="pr-4">{u.role}</td>
                  <td className="pr-4">{u.is_verified ? "yes" : "no"}</td>
                  <td className="pr-4"><StatusBadge status={u.is_active ? "ok" : "inactive"} /></td>
                  <td className="space-x-2 whitespace-nowrap py-2 text-right">
                    {u.id !== me?.id && (
                      <>
                        <button type="button" className="btn-secondary px-2 py-1 text-xs" onClick={() => void patch(u, { is_active: !u.is_active })}>{u.is_active ? "Deactivate" : "Activate"}</button>
                        <button type="button" className="btn-secondary px-2 py-1 text-xs" onClick={() => void patch(u, { role: u.role === "admin" ? "user" : "admin" })}>{u.role === "admin" ? "Make user" : "Make admin"}</button>
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="card">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="font-medium">Query audit log</h2>
          <div className="flex items-center gap-2 text-xs text-slate-500">
            <span>{total === 0 ? "0" : `${offset + 1}–${Math.min(offset + PAGE, total)}`} of {total}</span>
            <button type="button" className="btn-secondary px-2 py-1 text-xs" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Prev</button>
            <button type="button" className="btn-secondary px-2 py-1 text-xs" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>Next</button>
          </div>
        </div>
        <ul className="divide-y divide-slate-100 text-sm dark:divide-slate-800">
          {(audit.data?.items ?? []).map((a) => (
            <li key={a.id} className="py-2">
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge status={a.status} />
                <span className="font-medium">{a.question}</span>
              </div>
              <div className="mt-1 text-xs text-slate-500">
                {a.user_email ?? "deleted user"} · {new Date(a.created_at).toLocaleString()} · {a.latency_ms} ms{a.cache_hit ? " · cached" : ""} · agents: {a.agents_used.join(", ") || "—"}
              </div>
              {a.error && <div className="text-xs text-red-600 dark:text-red-400">{a.error}</div>}
              {a.sql_executed.map((q, i) => (
                <pre key={i} className="mt-1 overflow-x-auto rounded bg-slate-100 p-2 text-xs dark:bg-slate-800">{q}</pre>
              ))}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
```

**File: `frontend/src/App.tsx`**
```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { BrowserRouter, Navigate, Outlet, Route, Routes, useLocation } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import Layout from "./components/Layout";
import Admin from "./pages/Admin";
import Ask from "./pages/Ask";
import Dashboard from "./pages/Dashboard";
import Datasets from "./pages/Datasets";
import Login from "./pages/Login";
import Register from "./pages/Register";
import Verify from "./pages/Verify";

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } } });

function RequireAuth() {
  const { user } = useAuth();
  const location = useLocation();
  return user ? <Outlet /> : <Navigate to="/login" replace state={{ from: location.pathname }} />;
}

function RequireAdmin({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  return user?.role === "admin" ? <>{children}</> : <Navigate to="/" replace />;
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route path="/register" element={<Register />} />
            <Route path="/verify" element={<Verify />} />
            <Route element={<RequireAuth />}>
              <Route element={<Layout />}>
                <Route index element={<Ask />} />
                <Route path="datasets" element={<Datasets />} />
                <Route path="dashboard" element={<Dashboard />} />
                <Route path="admin" element={<RequireAdmin><Admin /></RequireAdmin>} />
              </Route>
            </Route>
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </BrowserRouter>
      </AuthProvider>
    </QueryClientProvider>
  );
}
```

**File: `frontend/src/main.tsx`**
```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./index.css";
import { initTheme } from "./utils/theme";

initTheme();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
```

- [ ] **Step 6: Type-check, test and build locally in Docker**

Run: `NODE> npm run test && npm run build`
Expected: tests pass, `tsc --noEmit` reports no errors, `dist/` produced.

- [ ] **Step 7: Write the production image and nginx config**

**File: `frontend/nginx.conf`**
```nginx
server {
  listen 80;
  server_name _;
  root /usr/share/nginx/html;
  index index.html;
  client_max_body_size 60m;

  add_header X-Content-Type-Options "nosniff" always;
  add_header X-Frame-Options "DENY" always;
  add_header Referrer-Policy "no-referrer" always;
  add_header Content-Security-Policy "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'" always;

  location /api/ {
    proxy_pass http://backend:8000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 180s;
  }

  location /assets/ {
    expires 7d;
    add_header Cache-Control "public, immutable";
    try_files $uri =404;
  }

  location / {
    try_files $uri /index.html;
  }
}
```

**File: `frontend/Dockerfile`**
```dockerfile
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run test && npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
```

> Note: `add_header` inside `location /assets/` replaces the server-level security headers for those static files only; that's acceptable for hashed JS/CSS assets.

- [ ] **Step 8: Build the frontend image (runs the tests inside the build)**

Run: `docker compose build frontend`
Expected: build succeeds (vitest + tsc + vite build all pass inside the image).

- [ ] **Step 9: Commit**

```bash
git add frontend
git commit -m "feat(frontend): ask/datasets/dashboard/admin pages, ECharts views, nginx image"
```
