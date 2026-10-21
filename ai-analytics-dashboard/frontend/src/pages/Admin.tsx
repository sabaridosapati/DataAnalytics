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
