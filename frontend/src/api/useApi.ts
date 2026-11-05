import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, getJson } from "./client";

export type Loadable<T> =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ok"; data: T }
  | { status: "error"; error: ApiError; attempts: number };

type Settled<T> = { key: string; result: { status: "ok"; data: T } | { status: "error"; error: ApiError; attempts: number } };

/**
 * Fetch `path` (null = do nothing). Returns the state and a reload function. "loading" is derived: it is
 * whatever state applies while no result has settled for the current request key.
 */
export function useApi<T>(path: string | null): [Loadable<T>, () => void] {
  const [settled, setSettled] = useState<Settled<T> | null>(null);
  const [nonce, setNonce] = useState(0);
  const failures = useRef(0);
  const key = `${path}#${nonce}`;

  useEffect(() => {
    if (path === null) return;
    const ctrl = new AbortController();
    getJson<T>(path, ctrl.signal).then(
      (data) => {
        failures.current = 0;
        setSettled({ key, result: { status: "ok", data } });
      },
      (err: unknown) => {
        if (ctrl.signal.aborted) return;
        failures.current += 1;
        const error = err instanceof ApiError ? err : new ApiError(0, "Unexpected error", null, null);
        setSettled({ key, result: { status: "error", error, attempts: failures.current } });
      },
    );
    return () => ctrl.abort();
  }, [path, key]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  if (path === null) return [{ status: "idle" }, reload];
  return [settled !== null && settled.key === key ? settled.result : { status: "loading" }, reload];
}
