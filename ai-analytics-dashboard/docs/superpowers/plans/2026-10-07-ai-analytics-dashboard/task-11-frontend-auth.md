# Task 11: Frontend foundation — tooling, API client, auth, login/register/verify pages

**Files:**
- Create: `frontend/package.json`, `frontend/tsconfig.json`, `frontend/vite.config.ts`, `frontend/tailwind.config.js`, `frontend/postcss.config.js`, `frontend/index.html`, `frontend/.gitignore`, `frontend/.dockerignore`
- Create: `frontend/src/index.css`, `frontend/src/vite-env.d.ts`, `frontend/src/test/setup.ts`
- Create: `frontend/src/api/types.ts`, `frontend/src/api/client.ts`, `frontend/src/auth/AuthContext.tsx`, `frontend/src/utils/theme.ts`, `frontend/src/utils/csv.ts`
- Create: `frontend/src/pages/Login.tsx`, `Register.tsx`, `Verify.tsx`, `frontend/src/components/AuthShell.tsx`
- Test: `frontend/src/utils/csv.test.ts`, `frontend/src/pages/Login.test.tsx`

**Interfaces:**
- Consumes: backend API (`/api/auth/*`, error shape `{error: {code, message}}`).
- Produces `api<T>(path, {method?, body?, form?}) => Promise<T>` (prefixes `/api`, adds bearer token, throws `ApiError(status, code, message)`; on 401 with a token it clears the session and dispatches `auth:logout`).
- Produces `getToken()`, `setToken(token|null)`.
- Produces `AuthProvider`, `useAuth() => {user, login(identifier, password), completeAuth(tokenOut), logout()}`.
- Produces `initTheme()`, `toggleTheme() => boolean`, `useIsDark() => boolean`.
- Produces `toCsv(columns, rows) => string`, `downloadCsv(filename, columns, rows)`.
- Types in `api/types.ts` mirror backend responses (see file).

All commands run in Docker (no local Node needed): `docker run --rm -v "${PWD}/frontend:/app" -w /app node:20-alpine sh -c "<cmd>"`. Below this is abbreviated as **`NODE> <cmd>`**.

- [ ] **Step 1: Write tooling files**

**File: `frontend/package.json`**
```json
{
  "name": "ai-analytics-frontend",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc --noEmit && vite build",
    "test": "vitest run",
    "preview": "vite preview"
  },
  "dependencies": {
    "@tanstack/react-query": "^5.56.2",
    "echarts": "^5.5.1",
    "echarts-for-react": "^3.0.2",
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-router-dom": "^6.26.2"
  },
  "devDependencies": {
    "@testing-library/dom": "^10.4.0",
    "@testing-library/jest-dom": "^6.5.0",
    "@testing-library/react": "^16.0.1",
    "@types/react": "^18.3.5",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.1",
    "autoprefixer": "^10.4.20",
    "jsdom": "^25.0.0",
    "postcss": "^8.4.47",
    "tailwindcss": "^3.4.11",
    "typescript": "^5.5.4",
    "vite": "^5.4.6",
    "vitest": "^2.1.1"
  }
}
```

**File: `frontend/tsconfig.json`**
```json
{
  "compilerOptions": {
    "target": "ES2020",
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "strict": true,
    "noEmit": true,
    "skipLibCheck": true,
    "esModuleInterop": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noUnusedLocals": true,
    "types": ["vitest/globals", "@testing-library/jest-dom"]
  },
  "include": ["src", "vite.config.ts"]
}
```

**File: `frontend/vite.config.ts`**
```ts
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": "http://localhost:8000" } },
  test: { environment: "jsdom", globals: true, setupFiles: ["./src/test/setup.ts"] },
});
```

**File: `frontend/tailwind.config.js`**
```js
/** @type {import('tailwindcss').Config} */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: { extend: {} },
  plugins: [],
};
```

**File: `frontend/postcss.config.js`**
```js
export default { plugins: { tailwindcss: {}, autoprefixer: {} } };
```

**File: `frontend/index.html`**
```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>AI Analytics Dashboard</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

**File: `frontend/.gitignore`**
```gitignore
node_modules/
dist/
*.log
```

**File: `frontend/.dockerignore`**
```text
node_modules
dist
*.log
```

**File: `frontend/src/vite-env.d.ts`**
```ts
/// <reference types="vite/client" />
```

**File: `frontend/src/test/setup.ts`**
```ts
import "@testing-library/jest-dom/vitest";
```

**File: `frontend/src/index.css`**
```css
@tailwind base;
@tailwind components;
@tailwind utilities;

body {
  @apply bg-slate-50 text-slate-900 antialiased dark:bg-slate-950 dark:text-slate-100;
}

@layer components {
  .card {
    @apply rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900;
  }
  .btn {
    @apply inline-flex items-center justify-center gap-2 rounded-lg bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50;
  }
  .btn-secondary {
    @apply inline-flex items-center justify-center gap-2 rounded-lg border border-slate-300 px-3 py-2 text-sm hover:bg-slate-100 disabled:opacity-50 dark:border-slate-700 dark:hover:bg-slate-800;
  }
  .btn-danger {
    @apply inline-flex items-center gap-2 rounded-lg border border-red-300 px-3 py-1.5 text-sm text-red-700 hover:bg-red-50 dark:border-red-800 dark:text-red-300 dark:hover:bg-red-950;
  }
  .input {
    @apply w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/30 dark:border-slate-700 dark:bg-slate-900;
  }
  .label {
    @apply mb-1 block text-sm font-medium;
  }
  .error-text {
    @apply text-sm text-red-600 dark:text-red-400;
  }
  .badge {
    @apply inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium;
  }
}
```

- [ ] **Step 2: Install dependencies (creates package-lock.json)**

Run: `NODE> npm install --no-audit --no-fund`
Expected: `node_modules/` and `package-lock.json` created.

- [ ] **Step 3: Write the failing tests**

**File: `frontend/src/utils/csv.test.ts`**
```ts
import { toCsv } from "./csv";

describe("toCsv", () => {
  it("escapes quotes, commas and newlines", () => {
    expect(toCsv(["a", "b"], [["x,y", 'say "hi"'], ["line\nbreak", null]])).toBe(
      'a,b\r\n"x,y","say ""hi"""\r\n"line\nbreak",',
    );
  });

  it("neutralizes spreadsheet formulas but keeps numbers", () => {
    expect(toCsv(["v"], [["=SUM(A1)"], ["+cmd"], ["@x"], [-5], ["-3.5"]])).toBe("v\r\n'=SUM(A1)\r\n'+cmd\r\n'@x\r\n-5\r\n-3.5");
  });
});
```

**File: `frontend/src/pages/Login.test.tsx`**
```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "../auth/AuthContext";
import Login from "./Login";

function renderLogin() {
  return render(
    <AuthProvider>
      <MemoryRouter>
        <Login />
      </MemoryRouter>
    </AuthProvider>,
  );
}

describe("Login page", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows the server's error message on failed login", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        statusText: "Unauthorized",
        json: async () => ({ error: { code: "invalid_credentials", message: "Invalid email/username or password." } }),
      }),
    );
    renderLogin();
    fireEvent.change(screen.getByLabelText(/email or username/i), { target: { value: "admin" } });
    fireEvent.change(screen.getByLabelText(/^password$/i), { target: { value: "bad" } });
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
    expect(await screen.findByText("Invalid email/username or password.")).toBeInTheDocument();
  });

  it("sends identifier and password to the login endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      statusText: "OK",
      json: async () => ({ access_token: "t", token_type: "bearer", user: { id: 1, email: "a@b.c", username: "admin", role: "admin" } }),
    });
    vi.stubGlobal("fetch", fetchMock);
    renderLogin();
    fireEvent.change(screen.getByLabelText(/email or username/i), { target: { value: "admin" } });
    fireEvent.change(screen.getByLabelText(/^password$/i), { target: { value: "Test@123" } });
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/auth/login");
    expect(JSON.parse(init.body)).toEqual({ identifier: "admin", password: "Test@123" });
  });
});
```

- [ ] **Step 4: Run them to verify they fail**

Run: `NODE> npx vitest run`
Expected: FAIL — cannot resolve `./csv` / `./Login`.

- [ ] **Step 5: Implement types, client, auth context, utils**

**File: `frontend/src/api/types.ts`**
```ts
export type Role = "admin" | "user";

export interface User {
  id: number;
  email: string;
  username: string | null;
  role: Role;
}

export interface TokenOut {
  access_token: string;
  token_type: string;
  user: User;
}

export type DatasetStatus = "pending" | "processing" | "ready" | "failed";

export interface Dataset {
  id: number;
  name: string;
  slug: string;
  kind: string;
  source_filename: string;
  file_type: string;
  parent_upload_id: number | null;
  row_count: number;
  chunk_count: number;
  status: DatasetStatus;
  error: string | null;
  created_at: string;
}

export interface DatasetColumn {
  column_name: string;
  pg_type: string;
  sample_values: unknown[];
  description: string;
}

export interface DatasetDetail extends Dataset {
  columns: DatasetColumn[];
  preview: {
    columns?: string[];
    rows?: unknown[][];
    chunks?: { index: number; content: string; metadata: Record<string, unknown> }[];
  };
}

export type ChartType = "bar" | "line" | "area" | "pie" | "scatter" | "table" | "kpi";

export interface ChartSpec {
  type: ChartType;
  title: string;
  x: string | null;
  y: string[];
  series: string | null;
  data: { columns: string[]; rows: unknown[][] };
  source_sql?: string | null;
}

export interface ResultTable {
  step_id: string;
  title: string;
  columns: string[];
  rows: unknown[][];
  row_count: number;
  truncated: boolean;
}

export interface Source {
  chunk_id: number;
  source: string;
  page: number | null;
  snippet: string;
}

export interface StepInfo {
  id: string;
  agent: string;
  task: string;
  status: "ok" | "error" | "skipped";
  error: string | null;
  ms: number;
  cache_hit?: boolean;
}

export interface PlanInfo {
  intent: string;
  answerable: boolean;
  reason: string | null;
  steps: { id: string; agent: string; task: string; datasets: string[]; depends_on: string[] }[];
}

export interface QueryResponse {
  answer: string;
  answerable: boolean;
  unverified_numbers: string[];
  charts: ChartSpec[];
  tables: ResultTable[];
  sources: Source[];
  sql: string[];
  plan: PlanInfo | null;
  steps: StepInfo[];
  cache_hit: boolean;
  request_id: string;
}

export interface Pin {
  id: number;
  title: string;
  chart: ChartSpec;
  sql: string | null;
  created_by: number | null;
  created_at: string;
}

export interface AdminUser extends User {
  is_verified: boolean;
  is_active: boolean;
  created_at: string;
}

export interface AuditItem {
  id: number;
  user_email: string | null;
  question: string;
  status: string;
  error: string | null;
  latency_ms: number;
  cache_hit: boolean;
  agents_used: string[];
  sql_executed: string[];
  created_at: string;
}
```

**File: `frontend/src/api/client.ts`**
```ts
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
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
    throw new ApiError(res.status, data?.error?.code ?? "error", data?.error?.message ?? res.statusText ?? "Request failed");
  }
  return data as T;
}
```

**File: `frontend/src/auth/AuthContext.tsx`**
```tsx
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, getToken, setToken } from "../api/client";
import type { TokenOut, User } from "../api/types";

interface AuthState {
  user: User | null;
  login: (identifier: string, password: string) => Promise<void>;
  completeAuth: (t: TokenOut) => void;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);
const USER_KEY = "user";

function readUser(): User | null {
  try {
    const raw = sessionStorage.getItem(USER_KEY);
    return raw && getToken() ? (JSON.parse(raw) as User) : null;
  } catch {
    return null;
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(readUser);

  const completeAuth = useCallback((t: TokenOut) => {
    setToken(t.access_token);
    try {
      sessionStorage.setItem(USER_KEY, JSON.stringify(t.user));
    } catch {
      /* ignore */
    }
    setUser(t.user);
  }, []);

  const logout = useCallback(() => {
    setToken(null);
    try {
      sessionStorage.removeItem(USER_KEY);
    } catch {
      /* ignore */
    }
    setUser(null);
  }, []);

  const login = useCallback(
    async (identifier: string, password: string) => {
      completeAuth(await api<TokenOut>("/auth/login", { body: { identifier, password } }));
    },
    [completeAuth],
  );

  useEffect(() => {
    window.addEventListener("auth:logout", logout);
    return () => window.removeEventListener("auth:logout", logout);
  }, [logout]);

  const value = useMemo(() => ({ user, login, completeAuth, logout }), [user, login, completeAuth, logout]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
```

**File: `frontend/src/utils/theme.ts`**
```ts
import { useEffect, useState } from "react";

export function initTheme(): void {
  let stored: string | null = null;
  try {
    stored = localStorage.getItem("theme");
  } catch {
    /* ignore */
  }
  const prefersDark = typeof window.matchMedia === "function" && window.matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.classList.toggle("dark", stored ? stored === "dark" : prefersDark);
}

export function toggleTheme(): boolean {
  const dark = document.documentElement.classList.toggle("dark");
  try {
    localStorage.setItem("theme", dark ? "dark" : "light");
  } catch {
    /* ignore */
  }
  return dark;
}

export function useIsDark(): boolean {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains("dark"));
  useEffect(() => {
    const obs = new MutationObserver(() => setDark(document.documentElement.classList.contains("dark")));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => obs.disconnect();
  }, []);
  return dark;
}
```

**File: `frontend/src/utils/csv.ts`**
```ts
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
```

- [ ] **Step 6: Implement the auth pages**

**File: `frontend/src/components/AuthShell.tsx`**
```tsx
import type { ReactNode } from "react";

export default function AuthShell({ title, subtitle, children }: { title: string; subtitle?: string; children: ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center px-4 py-10">
      <div className="w-full max-w-md">
        <div className="mb-6 text-center">
          <div className="mx-auto mb-3 flex h-11 w-11 items-center justify-center rounded-xl bg-indigo-600 text-lg font-bold text-white">
            AI
          </div>
          <h1 className="text-2xl font-semibold">{title}</h1>
          {subtitle && <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">{subtitle}</p>}
        </div>
        <div className="card p-6">{children}</div>
      </div>
    </div>
  );
}
```

**File: `frontend/src/pages/Login.tsx`**
```tsx
import { useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import AuthShell from "../components/AuthShell";

export default function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(identifier.trim(), password);
      const from = (location.state as { from?: string } | null)?.from ?? "/";
      navigate(from, { replace: true });
    } catch (err) {
      if (err instanceof ApiError && err.code === "unverified") {
        navigate(`/verify?email=${encodeURIComponent(identifier.trim())}`);
        return;
      }
      setError(err instanceof Error ? err.message : "Sign in failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthShell title="AI Analytics Dashboard" subtitle="Sign in to explore your data">
      <form onSubmit={onSubmit} className="space-y-4">
        <div>
          <label htmlFor="identifier" className="label">Email or username</label>
          <input id="identifier" className="input" autoComplete="username" value={identifier} onChange={(e) => setIdentifier(e.target.value)} required />
        </div>
        <div>
          <label htmlFor="password" className="label">Password</label>
          <input id="password" type="password" className="input" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </div>
        {error && <p className="error-text" role="alert">{error}</p>}
        <button type="submit" className="btn w-full" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
      </form>
      <p className="mt-4 text-center text-sm text-slate-500 dark:text-slate-400">
        New here? <Link to="/register" className="font-medium text-indigo-600 hover:underline dark:text-indigo-400">Create an account</Link>
      </p>
    </AuthShell>
  );
}
```

**File: `frontend/src/pages/Register.tsx`**
```tsx
import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import AuthShell from "../components/AuthShell";

export default function Register() {
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (password !== confirm) {
      setError("Passwords do not match.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api("/auth/register", { body: { email: email.trim(), password } });
      navigate(`/verify?email=${encodeURIComponent(email.trim())}&sent=1`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Registration failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthShell title="Create your account" subtitle="We'll email you a 6-digit verification code">
      <form onSubmit={onSubmit} className="space-y-4">
        <div>
          <label htmlFor="email" className="label">Work email</label>
          <input id="email" type="email" className="input" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </div>
        <div>
          <label htmlFor="password" className="label">Password</label>
          <input id="password" type="password" className="input" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} required minLength={8} />
          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">At least 8 characters, with a letter and a digit.</p>
        </div>
        <div>
          <label htmlFor="confirm" className="label">Confirm password</label>
          <input id="confirm" type="password" className="input" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} required />
        </div>
        {error && <p className="error-text" role="alert">{error}</p>}
        <button type="submit" className="btn w-full" disabled={busy}>{busy ? "Sending code…" : "Register"}</button>
      </form>
      <p className="mt-4 text-center text-sm text-slate-500 dark:text-slate-400">
        Already registered? <Link to="/login" className="font-medium text-indigo-600 hover:underline dark:text-indigo-400">Sign in</Link>
      </p>
    </AuthShell>
  );
}
```

**File: `frontend/src/pages/Verify.tsx`**
```tsx
import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { TokenOut } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import AuthShell from "../components/AuthShell";

const COOLDOWN = 60;

export default function Verify() {
  const [params] = useSearchParams();
  const { completeAuth } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState(params.get("email") ?? "");
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(params.get("sent") ? "We sent a 6-digit code to your email." : null);
  const [busy, setBusy] = useState(false);
  const [cooldown, setCooldown] = useState(params.get("sent") ? COOLDOWN : 0);

  useEffect(() => {
    if (cooldown <= 0) return;
    const t = setTimeout(() => setCooldown((c) => c - 1), 1000);
    return () => clearTimeout(t);
  }, [cooldown]);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      completeAuth(await api<TokenOut>("/auth/verify", { body: { email: email.trim(), code } }));
      navigate("/", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Verification failed.");
    } finally {
      setBusy(false);
    }
  }

  async function resend() {
    setError(null);
    try {
      const r = await api<{ message: string }>("/auth/resend-code", { body: { email: email.trim() } });
      setInfo(r.message);
      setCooldown(COOLDOWN);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not resend the code.");
    }
  }

  return (
    <AuthShell title="Verify your email" subtitle="Enter the 6-digit code we emailed you">
      <form onSubmit={onSubmit} className="space-y-4">
        <div>
          <label htmlFor="email" className="label">Email</label>
          <input id="email" type="email" className="input" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </div>
        <div>
          <label htmlFor="code" className="label">Verification code</label>
          <input
            id="code"
            className="input text-center font-mono text-2xl tracking-[0.5em]"
            inputMode="numeric"
            autoComplete="one-time-code"
            maxLength={6}
            value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
            required
          />
        </div>
        {info && !error && <p className="text-sm text-emerald-600 dark:text-emerald-400">{info}</p>}
        {error && <p className="error-text" role="alert">{error}</p>}
        <button type="submit" className="btn w-full" disabled={busy || code.length !== 6}>{busy ? "Verifying…" : "Verify and continue"}</button>
      </form>
      <div className="mt-4 flex items-center justify-between text-sm">
        <button type="button" className="text-indigo-600 hover:underline disabled:text-slate-400 disabled:no-underline dark:text-indigo-400" onClick={resend} disabled={cooldown > 0 || !email}>
          {cooldown > 0 ? `Resend code in ${cooldown}s` : "Resend code"}
        </button>
        <Link to="/login" className="text-slate-500 hover:underline dark:text-slate-400">Back to sign in</Link>
      </div>
    </AuthShell>
  );
}
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `NODE> npx vitest run`
Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add frontend
git commit -m "feat(frontend): tooling, API client, auth context, login/register/verify pages"
```
