import { API_BASE, retryDelaySeconds } from "../lib/transforms";

/** An API failure. retryAfterSeconds is set for 429/503 (header `Retry-After` first, then body). */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string | null;
  readonly retryAfterSeconds: number | null;

  constructor(status: number, message: string, code: string | null, retryAfterSeconds: number | null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.retryAfterSeconds = retryAfterSeconds;
  }

  get retryable(): boolean {
    return this.retryAfterSeconds !== null;
  }
}

interface ErrorBody {
  title?: unknown;
  detail?: unknown;
  code?: unknown;
  retry_after_seconds?: unknown;
}

async function readBody(res: Response): Promise<ErrorBody> {
  try {
    const parsed: unknown = await res.json();
    return typeof parsed === "object" && parsed !== null ? (parsed as ErrorBody) : {};
  } catch {
    return {};
  }
}

/**
 * GET a JSON resource from the API. No credentials are attached here: the dev proxy / web server adds the
 * Authorization header, so no key or token ever exists in the client bundle.
 */
export async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, { signal, headers: { Accept: "application/json, application/geo+json" } });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new ApiError(0, "Could not reach the API", "network_error", null);
  }
  if (res.ok) return (await res.json()) as T;
  const body = await readBody(res);
  const bodySeconds = typeof body.retry_after_seconds === "number" ? body.retry_after_seconds : undefined;
  const retryable = res.status === 429 || res.status === 503;
  const message =
    typeof body.detail === "string" ? body.detail : typeof body.title === "string" ? body.title : `Request failed (${res.status})`;
  return Promise.reject(
    new ApiError(
      res.status,
      message,
      typeof body.code === "string" ? body.code : null,
      retryable ? retryDelaySeconds(res.headers.get("Retry-After"), bodySeconds) : null,
    ),
  );
}
