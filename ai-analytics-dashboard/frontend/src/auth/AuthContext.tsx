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
