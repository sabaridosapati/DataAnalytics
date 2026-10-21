import type { AnswerData } from "../api/types";
import StatusBadge from "./StatusBadge";

export default function HowPanel({ response }: { response: AnswerData }) {
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
