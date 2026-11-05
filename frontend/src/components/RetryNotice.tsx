import { useEffect, useState } from "react";
import type { ApiError } from "../api/client";

const MAX_AUTO_RETRIES = 3;

interface Props {
  what: string;
  error: ApiError;
  attempts: number;
  onRetry: () => void;
}

/**
 * Error state with retry. For 429/503 the delay comes from the API (Retry-After header, else the
 * retry_after_seconds body field) and the request is retried automatically a few times; the message is static
 * so screen readers hear it once, while the visible countdown is decorative.
 */
export function RetryNotice({ what, error, attempts, onRetry }: Props) {
  const delay = error.retryAfterSeconds;
  const auto = delay !== null && attempts <= MAX_AUTO_RETRIES;
  const [left, setLeft] = useState(delay ?? 0);

  useEffect(() => {
    if (!auto || delay === null) return;
    const tick = window.setInterval(() => setLeft((s) => Math.max(0, s - 1)), 1000);
    const fire = window.setTimeout(onRetry, delay * 1000);
    return () => {
      window.clearInterval(tick);
      window.clearTimeout(fire);
    };
  }, [auto, delay, onRetry]);

  const headline =
    error.status === 429
      ? `Too many requests while loading ${what}.`
      : error.status === 503
        ? `${what[0]?.toUpperCase() ?? ""}${what.slice(1)} temporarily unavailable.`
        : `Could not load ${what}.`;

  return (
    <div className="notice notice-error" role="alert" data-testid={`error-${what.replace(/\s+/g, "-")}`}>
      <p>
        {headline} {error.message}
        {auto ? ` Retrying automatically in ${delay} seconds.` : ""}
      </p>
      {auto ? (
        <p aria-hidden="true" className="countdown">
          Retrying in {left}s
        </p>
      ) : null}
      <button type="button" onClick={onRetry}>
        Retry now
      </button>
    </div>
  );
}
