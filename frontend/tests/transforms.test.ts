import { describe, expect, it } from "vitest";
import {
  bandFor,
  describeLoan,
  formatPrincipal,
  retryDelaySeconds,
  safeDocumentHref,
  sortRows,
  splitFeatures,
} from "../src/lib/transforms";
import type { Feature } from "../src/lib/types";

function feature(
  id: string,
  over: Partial<Feature["properties"]> = {},
  geometry: Feature["geometry"] = { type: "Point", coordinates: [-70.85, 41.66] },
): Feature {
  return {
    id,
    type: "Feature",
    geometry,
    properties: {
      loan_id: id,
      address_norm: `${id} MAIN ST`,
      lender: "ACME LLC",
      lender_type: "private_llc",
      principal: "100000.00",
      recorded_date: "2024-01-02",
      maturity_date: "2025-01-02",
      term_months: 12,
      bridge_score: 50,
      is_bridge_loan: true,
      classifier_version: "placeholder-all-true",
      signals: [],
      geocode_status: "matched",
      score_band: "mid",
      ...over,
    },
  };
}

describe("bandFor", () => {
  it.each([
    [0, "low"],
    [39, "low"],
    [40, "mid"],
    [69, "mid"],
    [70, "high"],
    [100, "high"],
  ] as const)("score %i is %s", (score, band) => {
    expect(bandFor(score)).toBe(band);
  });
});

describe("formatPrincipal", () => {
  it("formats decimal strings as USD without float rounding", () => {
    expect(formatPrincipal("545930.00")).toBe("$545,930");
    expect(formatPrincipal("1234.50")).toBe("$1,234.50");
  });
  it("returns 'unknown' for null or junk", () => {
    expect(formatPrincipal(null)).toBe("unknown");
    expect(formatPrincipal("abc")).toBe("unknown");
  });
});

describe("splitFeatures", () => {
  it("separates mapped from unmapped (null geometry) and keeps order", () => {
    const fc = [feature("a"), feature("b", { geocode_status: "no_match" }, null), feature("c")];
    const out = splitFeatures(fc);
    expect(out.rows.map((r) => r.loanId)).toEqual(["a", "b", "c"]);
    expect(out.rows.map((r) => r.mapped)).toEqual([true, false, true]);
    expect(out.unmapped).toHaveLength(1);
    expect(out.rows[0]?.lon).toBe(-70.85);
    expect(out.rows[1]?.lon).toBeNull();
  });
});

describe("sortRows", () => {
  const rows = splitFeatures([
    feature("a", { bridge_score: 10, principal: "5.00", address_norm: "B" }),
    feature("b", { bridge_score: 90, principal: null, address_norm: null }),
    feature("c", { bridge_score: 90, principal: "9.00", address_norm: "A" }),
  ]).rows;
  it("sorts score desc with loan_id tiebreak", () => {
    expect(sortRows(rows, "score", "desc").map((r) => r.loanId)).toEqual(["b", "c", "a"]);
  });
  it("sorts ascending and puts nulls last in both directions", () => {
    expect(sortRows(rows, "principal", "asc").map((r) => r.loanId)).toEqual(["a", "c", "b"]);
    expect(sortRows(rows, "principal", "desc").map((r) => r.loanId)).toEqual(["c", "a", "b"]);
    expect(sortRows(rows, "address", "asc").map((r) => r.loanId)).toEqual(["c", "a", "b"]);
  });
  it("does not mutate its input", () => {
    const copy = [...rows];
    sortRows(rows, "score", "asc");
    expect(rows).toEqual(copy);
  });
});

describe("describeLoan", () => {
  it("builds an ARIA label with address, lender, principal and score", () => {
    const row = splitFeatures([feature("a")]).rows[0]!;
    const label = describeLoan(row);
    expect(label).toContain("a MAIN ST");
    expect(label).toContain("ACME LLC");
    expect(label).toContain("$100,000");
    expect(label).toContain("score 50");
  });
  it("falls back for missing fields", () => {
    const row = splitFeatures([feature("a", { address_norm: null, lender: null, principal: null })]).rows[0]!;
    expect(describeLoan(row)).toContain("address unknown");
  });
});

describe("safeDocumentHref", () => {
  it("only allows same-origin absolute paths and prefixes the API base", () => {
    expect(safeDocumentHref("/loans/x/source")).toBe("/api/loans/x/source");
  });
  it.each(["javascript:alert(1)", "//evil.test/x", "https://evil.test/x", "loans/x", "", "/\\evil"])(
    "rejects %s",
    (bad) => {
      expect(safeDocumentHref(bad)).toBeNull();
    },
  );
});

describe("retryDelaySeconds", () => {
  it("prefers the Retry-After header when valid", () => {
    expect(retryDelaySeconds("7", 3)).toBe(7);
  });
  it("falls back to the body field", () => {
    expect(retryDelaySeconds(null, 3)).toBe(3);
    expect(retryDelaySeconds("soon", 4)).toBe(4);
  });
  it("defaults and clamps", () => {
    expect(retryDelaySeconds(null, undefined)).toBe(5);
    expect(retryDelaySeconds("0", undefined)).toBe(1);
    expect(retryDelaySeconds("99999", undefined)).toBe(300);
  });
});
