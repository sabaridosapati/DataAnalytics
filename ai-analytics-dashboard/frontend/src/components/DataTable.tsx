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
