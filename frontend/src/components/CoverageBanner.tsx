import { useApi } from "../api/useApi";
import type { Coverage, TownCoverage } from "../lib/types";
import { RetryNotice } from "./RetryNotice";

function Town({ t }: { t: TownCoverage }) {
  return (
    <li>
      <strong>{t.town}</strong>: {t.documents_fetched} documents fetched.{" "}
      {t.gaps === null ? (
        <span>Gaps: unknown (no gap data in this snapshot).</span>
      ) : (
        <span>
          Gaps: <span data-testid="gap-paid">{t.gaps.paid} paid</span>,{" "}
          <span data-testid="gap-blocked">{t.gaps.blocked} blocked</span>,{" "}
          <span data-testid="gap-failed">{t.gaps.failed} failed</span>.
        </span>
      )}{" "}
      Last ingested: {t.last_ingested_at ?? "never"}.
    </li>
  );
}

export function CoverageBanner() {
  const [state, reload] = useApi<Coverage>("/coverage");
  return (
    <section className="coverage" aria-labelledby="coverage-title" aria-busy={state.status === "loading"}>
      <h2 id="coverage-title">Coverage</h2>
      {state.status === "loading" ? <p>Loading coverage...</p> : null}
      {state.status === "error" ? <RetryNotice what="coverage" error={state.error} attempts={state.attempts} onRetry={reload} /> : null}
      {state.status === "ok" ? (
        state.data.towns.length === 0 ? (
          <p>No towns ingested yet.</p>
        ) : (
          <ul>
            {state.data.towns.map((t) => (
              <Town key={t.town} t={t} />
            ))}
          </ul>
        )
      ) : null}
    </section>
  );
}
