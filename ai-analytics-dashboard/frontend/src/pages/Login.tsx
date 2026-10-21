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
