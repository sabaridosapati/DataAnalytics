export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public chatId?: number,
  ) {
    super(message);
  }
}

const TOKEN_KEY = "token";

function readToken(): string | null {
  try {
    return sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

let token: string | null = readToken();

export function getToken(): string | null {
  return token;
}

export function setToken(value: string | null): void {
  token = value;
  try {
    if (value) sessionStorage.setItem(TOKEN_KEY, value);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable: keep the token in memory only */
  }
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  form?: FormData;
}

export async function api<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  let body: BodyInit | undefined;
  if (opts.form) {
    body = opts.form;
  } else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }
  const res = await fetch(`/api${path}`, { method: opts.method ?? (body ? "POST" : "GET"), headers, body });
  if (res.status === 401 && token) {
    setToken(null);
    window.dispatchEvent(new Event("auth:logout"));
  }
  const data = res.status === 204 ? null : await res.json().catch(() => null);
  if (!res.ok) {
    throw new ApiError(res.status, data?.error?.code ?? "error", data?.error?.message ?? res.statusText ?? "Request failed", data?.error?.chat_id);
  }
  return data as T;
}
