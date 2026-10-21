import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { BrowserRouter, Navigate, Outlet, Route, Routes, useLocation } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import Layout from "./components/Layout";
import Admin from "./pages/Admin";
import Ask from "./pages/Ask";
import Dashboard from "./pages/Dashboard";
import Datasets from "./pages/Datasets";
import Login from "./pages/Login";
import Register from "./pages/Register";
import Verify from "./pages/Verify";

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } } });

function RequireAuth() {
  const { user } = useAuth();
  const location = useLocation();
  return user ? <Outlet /> : <Navigate to="/login" replace state={{ from: location.pathname }} />;
}

function RequireAdmin({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  return user?.role === "admin" ? <>{children}</> : <Navigate to="/" replace />;
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route path="/register" element={<Register />} />
            <Route path="/verify" element={<Verify />} />
            <Route element={<RequireAuth />}>
              <Route element={<Layout />}>
                <Route index element={<Ask />} />
                <Route path="datasets" element={<Datasets />} />
                <Route path="dashboard" element={<Dashboard />} />
                <Route path="admin" element={<RequireAdmin><Admin /></RequireAdmin>} />
              </Route>
            </Route>
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </BrowserRouter>
      </AuthProvider>
    </QueryClientProvider>
  );
}
