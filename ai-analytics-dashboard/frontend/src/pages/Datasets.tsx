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
