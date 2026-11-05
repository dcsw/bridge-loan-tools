import { describe, expect, it } from "vitest";
import { DEFAULT_URL_STATE, parseUrlState, serializeUrlState, toApiQuery } from "../src/lib/url-state";

describe("parseUrlState", () => {
  it("returns defaults for an empty query", () => {
    expect(parseUrlState("")).toEqual(DEFAULT_URL_STATE);
  });
  it("parses all filters, sort, view and selection", () => {
    const s = parseUrlState(
      "?town=mattapoisett&min_score=40&lender_type=bank&recorded_from=2024-01-01&recorded_to=2024-12-31&view=unmapped&sort=principal&dir=asc&selected=abc-0",
    );
    expect(s.filters).toEqual({
      town: "mattapoisett",
      minScore: 40,
      lenderType: "bank",
      recordedFrom: "2024-01-01",
      recordedTo: "2024-12-31",
    });
    expect(s.view).toBe("unmapped");
    expect(s.sort).toEqual({ key: "principal", dir: "asc" });
    expect(s.selected).toBe("abc-0");
  });
  it("drops invalid values instead of passing them to the API", () => {
    const s = parseUrlState(
      "?town=atlantis&min_score=150&lender_type=mob&recorded_from=yesterday&recorded_to=2024-02-30&sort=bogus&dir=up&view=x",
    );
    expect(s).toEqual(DEFAULT_URL_STATE);
  });
  it("drops non-integer or negative min_score", () => {
    expect(parseUrlState("?min_score=4.5").filters.minScore).toBeNull();
    expect(parseUrlState("?min_score=-1").filters.minScore).toBeNull();
  });
});

describe("serializeUrlState", () => {
  it("omits defaults", () => {
    expect(serializeUrlState(DEFAULT_URL_STATE)).toBe("");
  });
  it("round-trips", () => {
    const state = parseUrlState(
      "?town=rye&min_score=70&recorded_from=2024-01-01&view=unmapped&sort=lender&dir=desc&selected=x",
    );
    expect(parseUrlState(serializeUrlState(state))).toEqual(state);
  });
  it("encodes selected ids safely", () => {
    const q = serializeUrlState({ ...DEFAULT_URL_STATE, selected: "a b&c" });
    expect(q).toBe("?selected=a+b%26c");
    expect(parseUrlState(q).selected).toBe("a b&c");
  });
});

describe("toApiQuery", () => {
  it("emits only set filters, always a limit of 2000", () => {
    expect(toApiQuery(DEFAULT_URL_STATE.filters)).toBe("limit=2000");
    expect(toApiQuery({ ...DEFAULT_URL_STATE.filters, town: "rye", minScore: 0 })).toBe(
      "limit=2000&town=rye&min_score=0",
    );
  });
});
