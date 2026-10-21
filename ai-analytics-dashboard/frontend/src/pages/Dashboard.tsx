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
