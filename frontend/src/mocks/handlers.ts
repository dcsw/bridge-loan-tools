import { HttpResponse, http } from "msw";
import { COVERAGE, detailResponse, listResponse } from "./data";
import type { ListFilters } from "./data";

/**
 * Scenario switch for e2e tests (window.__mockScenario). "-once" scenarios apply to the next matching
 * request only. Default is a healthy API.
 */
export type Scenario =
  | "ok"
  | "empty"
  | "partial"
  | "server-error"
  | "rate-limit-once"
  | "rate-limit-body-only-once"
  | "unavailable-once"
  | "detail-unavailable-once"
  | "coverage-error"
  | "hold";

let scenario: Scenario = "ok";
export const setScenario = (s: Scenario): void => {
  scenario = s;
};

// "hold" parks the next /loans response until releaseHold() is called, so tests can observe the loading state
// deterministically (no timers).
let release: (() => void) | null = null;
const gate = new Promise<void>((resolve) => {
  release = resolve;
});
export const releaseHold = (): void => {
  scenario = "ok";
  release?.();
};

function problem(status: number, code: string, title: string, extra: Record<string, unknown> = {}, headers: Record<string, string> = {}) {
  return HttpResponse.json(
    { type: `https://errors.example.test/${code}`, title, status, code, errors: [], ...extra },
    { status, headers: { "Content-Type": "application/problem+json", ...headers } },
  );
}

function filtersFrom(url: URL): ListFilters {
  const g = (k: string) => url.searchParams.get(k);
  const score = g("min_score");
  return {
    town: g("town"),
    minScore: score === null ? null : Number(score),
    lenderType: g("lender_type"),
    recordedFrom: g("recorded_from"),
    recordedTo: g("recorded_to"),
  };
}

export const handlers = [
  http.get("/api/loans", async ({ request }) => {
    if (scenario === "hold") await gate;
    switch (scenario) {
      case "rate-limit-once":
        scenario = "ok";
        return problem(429, "rate_limited", "Too many requests", { retry_after_seconds: 2 }, { "Retry-After": "2" });
      case "rate-limit-body-only-once":
        scenario = "ok";
        return problem(429, "rate_limited", "Too many requests", { retry_after_seconds: 3 });
      case "unavailable-once":
        scenario = "ok";
        return problem(503, "snapshot_unavailable", "Snapshot unavailable", {}, { "Retry-After": "2" });
      case "server-error":
        return problem(500, "internal_error", "Internal error");
      case "empty":
        return HttpResponse.json({ ...listResponse(filtersFrom(new URL(request.url)), false), features: [], total: 0, unmapped_count: 0 });
      default:
        return HttpResponse.json(listResponse(filtersFrom(new URL(request.url)), scenario === "partial"));
    }
  }),
  http.get("/api/loans/:id", ({ params }) => {
    if (scenario === "detail-unavailable-once") {
      scenario = "ok";
      return problem(503, "snapshot_unavailable", "Snapshot unavailable", { retry_after_seconds: 2 }, { "Retry-After": "2" });
    }
    const detail = detailResponse(String(params.id));
    return detail ? HttpResponse.json(detail) : problem(404, "not_found", "Loan not found");
  }),
  http.get("/api/loans/:id/source", () => new HttpResponse("synthetic mock document", { headers: { "Content-Type": "text/plain" } })),
  http.get("/api/coverage", () =>
    scenario === "coverage-error" ? problem(503, "snapshot_unavailable", "Snapshot unavailable", {}, { "Retry-After": "2" }) : HttpResponse.json(COVERAGE),
  ),
];
