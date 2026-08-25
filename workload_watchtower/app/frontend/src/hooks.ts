import { useCallback, useEffect, useRef, useState } from "react";

// useApi — fetch-on-mount with manual refresh; optional auto-refresh interval.
export function useApi<T>(fetcher: () => Promise<T>, opts: { intervalMs?: number; deps?: unknown[] } = {}) {
  const { intervalMs, deps = [] } = opts;
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  // keep the latest fetcher without forcing effect re-runs
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const res = await fetcherRef.current();
      setData(res);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    if (intervalMs) {
      const t = setInterval(() => load(true), intervalMs);
      return () => clearInterval(t);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, error, loading, refresh: () => load(false), refreshQuiet: () => load(true) };
}

const THEME_KEY = "wt-theme";
export function useTheme() {
  const [dark, setDark] = useState<boolean>(() => {
    try {
      const saved = localStorage.getItem(THEME_KEY);
      if (saved) return saved === "dark";
    } catch {
      /* ignore */
    }
    return true; // dark-first
  });
  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    try {
      localStorage.setItem(THEME_KEY, dark ? "dark" : "light");
    } catch {
      /* ignore */
    }
  }, [dark]);
  return { dark, toggle: () => setDark((d) => !d) };
}
