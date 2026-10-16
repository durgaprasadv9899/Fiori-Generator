// Small data hooks for the console pages: fetch on mount / when deps change, optional polling.
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../api/client";

export function useApi<T = any>(path: string | null, opts: { poll?: number; deps?: unknown[] } = {}) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(!!path);
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);

  const reload = useCallback(async (silent = false) => {
    if (!path) return;
    if (!silent) setLoading(true);
    try {
      const d = await api.get<T>(path);
      if (alive.current) { setData(d); setError(""); }
    } catch (e: any) {
      if (alive.current) setError(e.message || String(e));
    } finally {
      if (alive.current) setLoading(false);
    }
  }, [path]);

  useEffect(() => { reload(); }, [reload, ...(opts.deps || [])]);         // eslint-disable-line
  useEffect(() => {
    if (!opts.poll || !path) return;
    const t = setInterval(() => reload(true), opts.poll);
    return () => clearInterval(t);
  }, [opts.poll, path, reload]);
  return { data, error, loading, reload, setData };
}

/** Download a file from the API (keeps the session cookie; works behind the approuter). */
export function download(path: string) {
  const a = document.createElement("a");
  a.href = api.url(path);
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

export function fmtDate(ts?: number | null): string {
  if (!ts) return "";
  return new Date(ts * 1000).toLocaleString(undefined, { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}
