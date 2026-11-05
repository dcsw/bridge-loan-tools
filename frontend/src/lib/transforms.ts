import type { Feature, LoanRow, ScoreBand, SortDir, SortKey } from "./types";

export const API_BASE = "/api";

/** Mirrors the backend score_band: 0-39 low, 40-69 mid, 70-100 high. */
export function bandFor(score: number): ScoreBand {
  if (score >= 70) return "high";
  if (score >= 40) return "mid";
  return "low";
}

const USD_WHOLE = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });

/** principal arrives as a decimal string; format by string so large values never pass through float math. */
export function formatPrincipal(principal: string | null): string {
  if (principal === null || !/^\d+(\.\d+)?$/.test(principal)) return "unknown";
  const [whole = "0", frac = ""] = principal.split(".");
  const cents = frac.replace(/0+$/, "");
  const grouped = USD_WHOLE.format(BigInt(whole)).replace(/\.00$/, "");
  return cents ? `${grouped}.${cents.padEnd(2, "0")}` : grouped;
}

export function splitFeatures(features: Feature[]): { rows: LoanRow[]; unmapped: LoanRow[] } {
  const rows = features.map<LoanRow>((f) => {
    const coords = f.geometry?.coordinates;
    return {
      loanId: f.properties.loan_id,
      props: f.properties,
      mapped: coords !== undefined,
      lon: coords?.[0] ?? null,
      lat: coords?.[1] ?? null,
    };
  });
  return { rows, unmapped: rows.filter((r) => !r.mapped) };
}

function sortValue(row: LoanRow, key: SortKey): string | number | null {
  const p = row.props;
  switch (key) {
    case "address":
      return p.address_norm;
    case "lender":
      return p.lender;
    case "principal":
      return p.principal === null ? null : Number(p.principal);
    case "term":
      return p.term_months;
    case "recorded":
      return p.recorded_date;
    case "maturity":
      return p.maturity_date;
    case "score":
      return p.bridge_score;
  }
}

/** Stable, non-mutating sort. Nulls always sort last; ties break on loan_id (matches the API cursor order). */
export function sortRows(rows: readonly LoanRow[], key: SortKey, dir: SortDir): LoanRow[] {
  const sign = dir === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    const av = sortValue(a, key);
    const bv = sortValue(b, key);
    if (av === null && bv !== null) return 1;
    if (bv === null && av !== null) return -1;
    if (av !== null && bv !== null && av !== bv) return (av < bv ? -1 : 1) * sign;
    return a.loanId < b.loanId ? -1 : a.loanId > b.loanId ? 1 : 0;
  });
}

export function describeLoan(row: LoanRow): string {
  const p = row.props;
  return [
    p.address_norm ?? "address unknown",
    `lender ${p.lender ?? "unknown"}`,
    `principal ${formatPrincipal(p.principal)}`,
    `score ${p.bridge_score}`,
    `${p.score_band} band`,
  ].join(", ");
}

/** The only hrefs we render from API data: same-origin absolute paths, routed through the API proxy. */
export function safeDocumentHref(path: string): string | null {
  if (!path.startsWith("/") || path.startsWith("//") || path.includes("\\")) return null;
  return `${API_BASE}${path}`;
}

/** Retry delay for 429/503: the Retry-After header wins when valid, then the body field, then 5 s. */
export function retryDelaySeconds(header: string | null, body: number | undefined): number {
  const fromHeader = header !== null && /^\d+$/.test(header.trim()) ? Number(header) : undefined;
  const raw = fromHeader ?? body ?? 5;
  return Math.min(300, Math.max(1, Math.round(raw)));
}

export function formatDate(d: string | null): string {
  return d ?? "unknown";
}
