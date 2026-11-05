import type { LenderType, SortDir, SortKey, Town } from "./types";

export interface Filters {
  town: Town | null;
  minScore: number | null;
  lenderType: LenderType | null;
  recordedFrom: string | null;
  recordedTo: string | null;
}

export interface UrlState {
  filters: Filters;
  view: "all" | "unmapped";
  sort: { key: SortKey; dir: SortDir };
  selected: string | null;
}

export const TOWNS: readonly Town[] = ["mattapoisett", "rye", "madison"];
export const LENDER_TYPES: readonly LenderType[] = ["bank", "credit_union", "private_llc", "individual", "other", "unknown"];
export const SORT_KEYS: readonly SortKey[] = ["address", "lender", "principal", "term", "recorded", "maturity", "score"];

export const DEFAULT_URL_STATE: UrlState = {
  filters: { town: null, minScore: null, lenderType: null, recordedFrom: null, recordedTo: null },
  view: "all",
  sort: { key: "score", dir: "desc" },
  selected: null,
};

function oneOf<T extends string>(allowed: readonly T[], v: string | null): T | null {
  return v !== null && (allowed as readonly string[]).includes(v) ? (v as T) : null;
}

function isoDate(v: string | null): string | null {
  if (v === null || !/^\d{4}-\d{2}-\d{2}$/.test(v)) return null;
  const d = new Date(`${v}T00:00:00Z`);
  return !Number.isNaN(d.getTime()) && d.toISOString().startsWith(v) ? v : null;
}

/** Parse defensively: anything invalid is dropped so it can never reach the API as a bad param. */
export function parseUrlState(search: string): UrlState {
  const q = new URLSearchParams(search);
  const rawScore = q.get("min_score");
  const score = rawScore !== null && /^\d+$/.test(rawScore) ? Number(rawScore) : null;
  const key = oneOf(SORT_KEYS, q.get("sort"));
  const dir = oneOf(["asc", "desc"] as const, q.get("dir"));
  const sel = q.get("selected");
  return {
    filters: {
      town: oneOf(TOWNS, q.get("town")),
      minScore: score !== null && score <= 100 ? score : null,
      lenderType: oneOf(LENDER_TYPES, q.get("lender_type")),
      recordedFrom: isoDate(q.get("recorded_from")),
      recordedTo: isoDate(q.get("recorded_to")),
    },
    view: q.get("view") === "unmapped" ? "unmapped" : "all",
    sort: { key: key ?? DEFAULT_URL_STATE.sort.key, dir: dir ?? DEFAULT_URL_STATE.sort.dir },
    selected: sel !== null && sel.length > 0 && sel.length <= 200 ? sel : null,
  };
}

export function serializeUrlState(s: UrlState): string {
  const q = new URLSearchParams();
  const f = s.filters;
  if (f.town) q.set("town", f.town);
  if (f.minScore !== null) q.set("min_score", String(f.minScore));
  if (f.lenderType) q.set("lender_type", f.lenderType);
  if (f.recordedFrom) q.set("recorded_from", f.recordedFrom);
  if (f.recordedTo) q.set("recorded_to", f.recordedTo);
  if (s.view === "unmapped") q.set("view", "unmapped");
  if (s.sort.key !== DEFAULT_URL_STATE.sort.key) q.set("sort", s.sort.key);
  if (s.sort.dir !== DEFAULT_URL_STATE.sort.dir) q.set("dir", s.sort.dir);
  if (s.selected) q.set("selected", s.selected);
  const out = q.toString();
  return out ? `?${out}` : "";
}

/** Query string for GET /loans. The map loads up to the 2000 cap (no viewport fetching). */
export function toApiQuery(f: Filters): string {
  const q = new URLSearchParams({ limit: "2000" });
  if (f.town) q.set("town", f.town);
  if (f.minScore !== null) q.set("min_score", String(f.minScore));
  if (f.lenderType) q.set("lender_type", f.lenderType);
  if (f.recordedFrom) q.set("recorded_from", f.recordedFrom);
  if (f.recordedTo) q.set("recorded_to", f.recordedTo);
  return q.toString();
}
