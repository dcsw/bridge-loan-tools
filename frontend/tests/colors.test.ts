import { describe, expect, it } from "vitest";
import { BAND_STYLE, LAND_COLOR, contrastRatio, textColorFor } from "../src/lib/colors";

describe("contrastRatio", () => {
  it("matches the WCAG reference values", () => {
    expect(contrastRatio("#000000", "#ffffff")).toBeCloseTo(21, 0);
    expect(contrastRatio("#777777", "#ffffff")).toBeCloseTo(4.48, 1);
  });
});

describe("pin palette (WCAG AA)", () => {
  for (const [band, style] of Object.entries(BAND_STYLE)) {
    it(`${band}: badge text >= 4.5:1 on fill`, () => {
      expect(contrastRatio(textColorFor(style.fill), style.fill)).toBeGreaterThanOrEqual(4.5);
    });
    it(`${band}: outline >= 3:1 against the map land`, () => {
      expect(contrastRatio(style.stroke, LAND_COLOR)).toBeGreaterThanOrEqual(3);
    });
  }
  it("bands use distinct shapes and distinct fills (never color alone)", () => {
    const shapes = new Set(Object.values(BAND_STYLE).map((s) => s.shape));
    const fills = new Set(Object.values(BAND_STYLE).map((s) => s.fill));
    expect(shapes.size).toBe(3);
    expect(fills.size).toBe(3);
  });
});
