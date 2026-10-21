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
