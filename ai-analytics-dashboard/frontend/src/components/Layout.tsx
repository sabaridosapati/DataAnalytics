import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { toggleTheme, useIsDark } from "../utils/theme";

const linkClass = ({ isActive }: { isActive: boolean }) =>
  `block rounded-lg px-3 py-2 text-sm font-medium ${
    isActive ? "bg-indigo-600 text-white" : "text-slate-700 hover:bg-slate-200 dark:text-slate-300 dark:hover:bg-slate-800"
  }`;

export default function Layout() {
  const { user, logout } = useAuth();
  const dark = useIsDark();
  return (
    <div className="flex min-h-screen flex-col md:flex-row">
      <aside className="flex shrink-0 flex-col gap-1 border-b border-slate-200 bg-white p-4 md:w-60 md:border-b-0 md:border-r dark:border-slate-800 dark:bg-slate-900">
        <div className="mb-4 flex items-center gap-2">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-indigo-600 text-sm font-bold text-white">AI</div>
          <span className="font-semibold">AI Analytics</span>
        </div>
        <nav className="flex gap-1 overflow-x-auto md:flex-col">
          <NavLink to="/" end className={linkClass}>Ask</NavLink>
          <NavLink to="/datasets" className={linkClass}>Datasets</NavLink>
          <NavLink to="/dashboard" className={linkClass}>Dashboard</NavLink>
          {user?.role === "admin" && <NavLink to="/admin" className={linkClass}>Admin</NavLink>}
        </nav>
        <div className="mt-auto hidden space-y-2 pt-6 text-sm md:block">
          <div className="truncate text-slate-500 dark:text-slate-400" title={user?.email}>{user?.username ?? user?.email}</div>
          <div className="flex gap-2">
            <button type="button" className="btn-secondary flex-1 px-2 py-1 text-xs" onClick={() => toggleTheme()}>{dark ? "Light" : "Dark"} mode</button>
            <button type="button" className="btn-secondary flex-1 px-2 py-1 text-xs" onClick={logout}>Sign out</button>
          </div>
        </div>
      </aside>
      <main className="min-w-0 flex-1 p-4 md:p-8">
        <div className="mb-4 flex justify-end gap-2 md:hidden">
          <button type="button" className="btn-secondary px-2 py-1 text-xs" onClick={() => toggleTheme()}>{dark ? "Light" : "Dark"}</button>
          <button type="button" className="btn-secondary px-2 py-1 text-xs" onClick={logout}>Sign out</button>
        </div>
        <Outlet />
      </main>
    </div>
  );
}
