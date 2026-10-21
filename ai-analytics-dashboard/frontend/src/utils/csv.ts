const NUMERIC = /^-?\d+(\.\d+)?$/;

function escapeCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  let s = String(value);
  if (/^[=+\-@]/.test(s) && !NUMERIC.test(s)) s = `'${s}`;
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function toCsv(columns: string[], rows: unknown[][]): string {
  return [columns.map(escapeCell).join(","), ...rows.map((r) => r.map(escapeCell).join(","))].join("\r\n");
}

export function downloadCsv(filename: string, columns: string[], rows: unknown[][]): void {
  const blob = new Blob([toCsv(columns, rows)], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
