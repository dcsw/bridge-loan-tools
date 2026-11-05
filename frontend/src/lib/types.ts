import type { components } from "../api/schema";

type S = components["schemas"];

export type Feature = S["Feature"];
export type FeatureCollection = S["FeatureCollection"];
export type LoanProps = S["LoanFeatureProperties"];
export type LoanDetail = S["LoanDetail"];
export type Coverage = S["Coverage"];
export type TownCoverage = S["TownCoverage"];
export type Problem = S["Problem"];
export type FieldValue = S["FieldValue"];

export type ScoreBand = LoanProps["score_band"];
export type Signal = LoanProps["signals"][number];
export type Town = "mattapoisett" | "rye" | "madison";
export type LenderType = NonNullable<LoanProps["lender_type"]>;

/** A list feature flattened for table and map use. Contains no borrower data (list has none). */
export interface LoanRow {
  loanId: string;
  props: LoanProps;
  mapped: boolean;
  lon: number | null;
  lat: number | null;
}

export type SortKey = "address" | "lender" | "principal" | "term" | "recorded" | "maturity" | "score";
export type SortDir = "asc" | "desc";
