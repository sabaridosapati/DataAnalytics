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
