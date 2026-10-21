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
