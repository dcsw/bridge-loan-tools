import type { Coverage, Feature, FeatureCollection, FieldValue, LoanDetail } from "../lib/types";
import { bandFor } from "../lib/transforms";

/**
 * Mock data for MSW. The first loan mirrors tests/fixtures/snapshots/single (synthetic, no real PII).
 * The rest are invented so the map shows all three score bands and an unmapped row; the live classifier is
 * a placeholder that scores everything 100, so varied scores exist only in these mocks.
 */
interface Seed {
  n: number;
  address: string | null;
  lender: string | null;
  lenderType: Feature["properties"]["lender_type"];
  principal: string | null;
  recorded: string | null;
  maturity: string | null;
  term: number | null;
  score: number;
  signals: Feature["properties"]["signals"];
  unevaluable: Feature["properties"]["signals"];
  lon: number | null;
  lat: number | null;
  borrower: string | null;
}

const FIXTURE_ID = "43fad2cdf0a56d6820352049949b93f8b1a7f6c372236d8b5a3ef48e007ce651-0";

const SEEDS: Seed[] = [
  { n: 0, address: "180 SONIA PLAINS APT 842 MATTAPOISETT MA", lender: "ROBERTS INDUSTRY CAPITAL LLC", lenderType: "private_llc", principal: "545930.00", recorded: "2024-07-23", maturity: "2029-07-23", term: 60, score: 100, signals: ["short_term", "keyword"], unevaluable: ["purchase_then_mortgage_90d", "private_lender"], lon: -70.853387, lat: 41.6677, borrower: "Ann Mccoy" },
  { n: 1, address: "12 WATER ST MATTAPOISETT MA", lender: "SOUTH COAST CREDIT UNION", lenderType: "credit_union", principal: "310000.00", recorded: "2024-03-02", maturity: "2054-03-02", term: 360, score: 22, signals: [], unevaluable: ["no_discharge"], lon: -70.8, lat: 41.65, borrower: "Synthetic Borrower One" },
  { n: 2, address: "7 MARION RD MATTAPOISETT MA", lender: "HARBOR BANK NA", lenderType: "bank", principal: "725000.50", recorded: "2024-05-14", maturity: "2025-05-14", term: 12, score: 55, signals: ["short_term"], unevaluable: [], lon: -70.82, lat: 41.66, borrower: "Synthetic Borrower Two" },
  { n: 3, address: "44 CRANE AVE MATTAPOISETT MA", lender: "BAYSIDE BRIDGE FUND LLC", lenderType: "private_llc", principal: "980000.00", recorded: "2024-09-01", maturity: "2025-03-01", term: 6, score: 83, signals: ["short_term", "private_lender", "second_lien"], unevaluable: ["no_discharge"], lon: -70.78, lat: 41.67, borrower: "Synthetic Borrower Three" },
  { n: 4, address: "<img src=x onerror=alert(1)> 9 ELM ST", lender: "O'BRIEN & SONS <b>LLC</b>", lenderType: "individual", principal: "150000.00", recorded: "2023-11-20", maturity: null, term: null, score: 41, signals: ["keyword"], unevaluable: ["short_term"], lon: -70.84, lat: 41.64, borrower: "<script>alert(1)</script>" },
  { n: 5, address: "301 HAMMETT COVE RD MATTAPOISETT MA", lender: null, lenderType: null, principal: null, recorded: "2022-06-30", maturity: null, term: null, score: 10, signals: [], unevaluable: ["private_lender"], lon: -70.81, lat: 41.636, borrower: null },
  { n: 6, address: "15 NO MATCH LN MATTAPOISETT MA", lender: "UNMAPPED LENDING CO", lenderType: "other", principal: "400000.00", recorded: "2024-08-08", maturity: "2025-08-08", term: 12, score: 72, signals: ["short_term"], unevaluable: [], lon: null, lat: null, borrower: "Synthetic Borrower Seven" },
  { n: 7, address: null, lender: "ANOTHER UNMAPPED LLC", lenderType: "private_llc", principal: "275000.00", recorded: "2024-10-10", maturity: null, term: null, score: 35, signals: [], unevaluable: [], lon: null, lat: null, borrower: null },
];

export function loanIdFor(n: number): string {
  return n === 0 ? FIXTURE_ID : `${FIXTURE_ID.slice(0, 16)}${String(n).padStart(8, "0")}-0`;
}

function toFeature(s: Seed): Feature {
  return {
    id: loanIdFor(s.n),
    type: "Feature",
    geometry: s.lon === null || s.lat === null ? null : { type: "Point", coordinates: [s.lon, s.lat] },
    properties: {
      loan_id: loanIdFor(s.n),
      address_norm: s.address,
      lender: s.lender,
      lender_type: s.lenderType,
      principal: s.principal,
      recorded_date: s.recorded,
      maturity_date: s.maturity,
      term_months: s.term,
      bridge_score: s.score,
      is_bridge_loan: true,
      classifier_version: "placeholder-all-true",
      signals: s.signals,
      geocode_status: s.lon === null ? "no_match" : "matched",
      score_band: bandFor(s.score),
    },
  };
}

export const ALL_FEATURES: Feature[] = SEEDS.map(toFeature);

export interface ListFilters {
  town: string | null;
  minScore: number | null;
  lenderType: string | null;
  recordedFrom: string | null;
  recordedTo: string | null;
}

export function listResponse(f: ListFilters, partial: boolean): FeatureCollection {
  const matches = ALL_FEATURES.filter((x) => {
    const p = x.properties;
    if (f.town !== null && f.town !== "mattapoisett") return false;
    if (f.minScore !== null && p.bridge_score < f.minScore) return false;
    if (f.lenderType !== null && p.lender_type !== f.lenderType) return false;
    if (f.recordedFrom !== null && (p.recorded_date === null || p.recorded_date < f.recordedFrom)) return false;
    if (f.recordedTo !== null && (p.recorded_date === null || p.recorded_date > f.recordedTo)) return false;
    return true;
  }).sort((a, b) => b.properties.bridge_score - a.properties.bridge_score || (a.id < b.id ? -1 : 1));
  return {
    type: "FeatureCollection",
    features: matches,
    total: partial ? matches.length + 1500 : matches.length,
    unmapped_count: matches.filter((x) => x.geometry === null).length,
    next_cursor: partial ? "mock-cursor" : null,
    generated_at: "2026-01-01T00:00:00+00:00",
  };
}

export function detailResponse(id: string): LoanDetail | null {
  const seed = SEEDS.find((s) => loanIdFor(s.n) === id);
  if (!seed) return null;
  const p = toFeature(seed).properties;
  const fields: Record<string, FieldValue> = {
    principal: { value: p.principal, confidence: 0.97, source_extractor: "docling", disagreement: false },
    lender: { value: p.lender, confidence: 0.62, source_extractor: "unstructured", disagreement: true },
    maturity_date: { value: p.maturity_date, confidence: 0.9, source_extractor: "docling", disagreement: false },
  };
  const docId = id.replace(/-0$/, "");
  return {
    ...p,
    borrower: seed.borrower,
    signals_unevaluable: seed.unevaluable,
    fields,
    last_ingested_at: "2026-01-01T00:00:00Z",
    source: {
      doc_id: docId,
      recorded_book: "315",
      recorded_page: "275",
      source_url: `https://portal.example.test/docs/${docId}.pdf`,
      document_path: `/loans/${id}/source`,
    },
  };
}

export const COVERAGE: Coverage = {
  generated_at: "2026-01-01T00:00:00+00:00",
  towns: [
    { town: "mattapoisett", documents_fetched: 42, gaps: { paid: 3, blocked: 1, failed: 2 }, last_ingested_at: "2026-01-01T00:00:00+00:00" },
    { town: "rye", documents_fetched: 0, gaps: null, last_ingested_at: null },
  ],
};
