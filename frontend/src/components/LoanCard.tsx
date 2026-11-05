import { forwardRef, useState } from "react";
import { useApi } from "../api/useApi";
import { BAND_STYLE } from "../lib/colors";
import { formatDate, formatPrincipal, safeDocumentHref } from "../lib/transforms";
import type { LoanDetail, LoanRow, Signal } from "../lib/types";
import { RetryNotice } from "./RetryNotice";

const SIGNAL_LABEL: Record<Signal, string> = {
  purchase_then_mortgage_90d: "Purchase then mortgage within 90 days",
  second_lien: "Second lien",
  short_term: "Short term",
  private_lender: "Private lender",
  no_discharge: "No discharge recorded",
  keyword: "Bridge-loan keyword",
};

function SignalList({ signals, empty }: { signals: Signal[]; empty: string }) {
  return signals.length === 0 ? (
    <p>{empty}</p>
  ) : (
    <ul>
      {signals.map((s) => (
        <li key={s}>{SIGNAL_LABEL[s]}</li>
      ))}
    </ul>
  );
}

function Provenance({ detail }: { detail: LoanDetail }) {
  const entries = Object.entries(detail.fields);
  return (
    <section aria-labelledby="prov-title">
      <h4 id="prov-title">Field confidence</h4>
      {entries.length === 0 ? (
        <p>No per-field data.</p>
      ) : (
        <table className="prov">
          <thead>
            <tr>
              <th scope="col">Field</th>
              <th scope="col">Value</th>
              <th scope="col">Confidence</th>
              <th scope="col">Source</th>
              <th scope="col">Agreement</th>
            </tr>
          </thead>
          <tbody>
            {entries.map(([name, f]) => (
              <tr key={name}>
                <th scope="row">{name}</th>
                <td>{f.value === null || f.value === undefined ? "unknown" : String(f.value)}</td>
                <td>{Math.round(f.confidence * 100)}%</td>
                <td>{f.source_extractor ?? "unknown"}</td>
                <td>{f.disagreement ? "Extractors disagree" : "Extractors agree"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

interface Props {
  row: LoanRow;
  onClose: () => void;
}

/**
 * Side panel for one loan. The list payload has no borrower; the borrower (and the detail-only fields:
 * provenance, unevaluable signals, source link) arrive only after the user clicks "Show borrower",
 * which is the single call to the audited GET /loans/{id}.
 */
export const LoanCard = forwardRef<HTMLHeadingElement, Props>(function LoanCard({ row, onClose }, headingRef) {
  const p = row.props;
  const [wanted, setWanted] = useState(false);
  const [state, reload] = useApi<LoanDetail>(wanted ? `/loans/${encodeURIComponent(row.loanId)}` : null);

  const band = BAND_STYLE[p.score_band];
  const detail = state.status === "ok" ? state.data : null;
  const href = detail ? safeDocumentHref(detail.source.document_path) : null;

  return (
    <aside className="card" role="dialog" aria-modal="false" aria-labelledby="card-title" data-testid="loan-card">
      <div className="card-head">
        <h3 id="card-title" tabIndex={-1} ref={headingRef}>
          {p.address_norm ?? "Address unknown"}
        </h3>
        <button type="button" onClick={onClose} aria-label="Close loan details">
          Close
        </button>
      </div>
      <dl>
        <dt>Lender</dt>
        <dd>
          {p.lender ?? "unknown"}
          {p.lender_type ? ` (${p.lender_type.replace("_", " ")})` : ""}
        </dd>
        <dt>Principal</dt>
        <dd>{formatPrincipal(p.principal)}</dd>
        <dt>Term</dt>
        <dd>{p.term_months === null ? "unknown" : `${p.term_months} months`}</dd>
        <dt>Recorded</dt>
        <dd>{formatDate(p.recorded_date)}</dd>
        <dt>Maturity</dt>
        <dd>{formatDate(p.maturity_date)}</dd>
        <dt>Score</dt>
        <dd>
          {p.bridge_score} ({band.label} band, {band.range})
        </dd>
        <dt>Bridge loan</dt>
        <dd>{p.is_bridge_loan ? "Yes" : "No"} (unverified placeholder classifier)</dd>
        <dt>Classifier version</dt>
        <dd>{p.classifier_version}</dd>
        <dt>Geocode</dt>
        <dd>{p.geocode_status.replace("_", " ")}</dd>
      </dl>

      <section aria-labelledby="fired-title">
        <h4 id="fired-title">Signals fired</h4>
        <SignalList signals={p.signals} empty="No signals fired." />
      </section>

      <section aria-labelledby="detail-title" aria-busy={state.status === "loading"}>
        <h4 id="detail-title">Borrower and record details</h4>
        {!wanted ? (
          <>
            <p>The borrower name is personal data and is only loaded on request. This also loads field confidence, signals that could not be evaluated, and the source document link.</p>
            <button type="button" onClick={() => setWanted(true)}>
              Show borrower
            </button>
          </>
        ) : null}
        {wanted && state.status === "loading" ? <p>Loading details...</p> : null}
        {wanted && state.status === "error" ? (
          <RetryNotice what="loan details" error={state.error} attempts={state.attempts} onRetry={reload} />
        ) : null}
        {detail ? (
          <>
            <p>
              Borrower: <strong data-testid="borrower">{detail.borrower ?? "none recorded"}</strong>{" "}
              <button type="button" onClick={() => setWanted(false)}>
                Hide borrower
              </button>
            </p>
            <h4>Signals not evaluable</h4>
            <SignalList signals={detail.signals_unevaluable} empty="All signals could be evaluated." />
            <Provenance detail={detail} />
            <p>
              Source document: book {detail.source.recorded_book ?? "?"}, page {detail.source.recorded_page ?? "?"}{" "}
              {href ? (
                <a href={href} target="_blank" rel="noopener noreferrer">
                  Open source document (new tab)
                </a>
              ) : (
                "(link unavailable)"
              )}
            </p>
          </>
        ) : null}
      </section>
    </aside>
  );
});
