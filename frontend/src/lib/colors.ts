import type { ScoreBand } from "./types";

export type PinShape = "circle" | "square" | "triangle";

export interface BandStyle {
  label: string;
  range: string;
  shape: PinShape;
  fill: string;
  stroke: string;
}

/** Land fill of the map; pin outlines must keep >= 3:1 against it (WCAG 1.4.11). */
export const LAND_COLOR = "#e8eef2";

/**
 * Viridis-derived, luminance-ordered fills (safe for the common color-vision deficiencies), each with a
 * distinct shape and a numeric badge so color is never the only cue.
 */
export const BAND_STYLE: Record<ScoreBand, BandStyle> = {
  low: { label: "Low", range: "0-39", shape: "circle", fill: "#fde725", stroke: "#000000" },
  mid: { label: "Medium", range: "40-69", shape: "square", fill: "#21908c", stroke: "#000000" },
  high: { label: "High", range: "70-100", shape: "triangle", fill: "#440154", stroke: "#000000" },
};

function channel(c: number): number {
  const s = c / 255;
  return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}

function luminance(hex: string): number {
  const n = parseInt(hex.slice(1), 16);
  return 0.2126 * channel((n >> 16) & 255) + 0.7152 * channel((n >> 8) & 255) + 0.0722 * channel(n & 255);
}

export function contrastRatio(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

/** Whichever of black/white gives the better contrast on the given fill. */
export function textColorFor(fill: string): string {
  return contrastRatio("#000000", fill) >= contrastRatio("#ffffff", fill) ? "#000000" : "#ffffff";
}
