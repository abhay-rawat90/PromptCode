import { useCallback, useEffect, useRef, useState } from "react";
import { api, isTransient } from "./api";

interface State<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  /** Re-fetch, keeping the current data on screen while loading. */
  reload: () => void;
}

/**
 * Fetch `path` and keep it fresh. Unlike a bare useEffect + api() call it:
 * - ignores responses from stale requests (fast navigation, React StrictMode double effects),
 * - clears the error as soon as a request succeeds,
 * - after a connection-type failure, retries when the tab regains focus or the network returns.
 * Pass `null` as the path to skip fetching; change `key` to force a refetch of the same path.
 */
export function useApi<T>(path: string | null, key?: unknown): State<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(path !== null);
  const [nonce, setNonce] = useState(0);
  const lastError = useRef<unknown>(null);

  const reload = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    if (path === null) return;
    let current = true;
    setLoading(true);
    api<T>(path)
      .then((d) => {
        if (!current) return;
        setData(d);
        setError(null);
        lastError.current = null;
      })
      .catch((e) => {
        if (!current) return;
        setError((e as Error).message);
        lastError.current = e;
      })
      .finally(() => current && setLoading(false));
    return () => {
      current = false;
    };
  }, [path, nonce, key]);

  // Self-heal after the backend or network comes back.
  useEffect(() => {
    const retry = () => isTransient(lastError.current) && reload();
    window.addEventListener("focus", retry);
    window.addEventListener("online", retry);
    const timer = window.setInterval(retry, 5_000);
    return () => {
      window.removeEventListener("focus", retry);
      window.removeEventListener("online", retry);
      window.clearInterval(timer);
    };
  }, [reload]);

  return { data, error, loading, reload };
}
