import { BAND_STYLE, textColorFor } from "../lib/colors";
import type { PinShape } from "../lib/colors";
import type { ScoreBand } from "../lib/types";

export function ShapeIcon({ shape, fill, stroke, label }: { shape: PinShape; fill: string; stroke: string; label?: string }) {
  return (
    <svg width="34" height="34" viewBox="0 0 34 34" aria-hidden="true" focusable="false">
      {shape === "circle" ? <circle cx="17" cy="17" r="14" fill={fill} stroke={stroke} strokeWidth="2" /> : null}
      {shape === "square" ? <rect x="4" y="4" width="26" height="26" rx="3" fill={fill} stroke={stroke} strokeWidth="2" /> : null}
      {shape === "triangle" ? <polygon points="17,3 31,30 3,30" fill={fill} stroke={stroke} strokeWidth="2" strokeLinejoin="round" /> : null}
      {label !== undefined ? (
        <text x="17" y={shape === "triangle" ? 25 : 21} textAnchor="middle" fontSize="11" fontWeight="700" fill={textColorFor(fill)}>
          {label}
        </text>
      ) : null}
    </svg>
  );
}

export function Legend({ classifierVersions }: { classifierVersions: string[] }) {
  const bands: ScoreBand[] = ["low", "mid", "high"];
  return (
    <section className="legend" aria-labelledby="legend-title">
      <h2 id="legend-title">Legend: bridge score</h2>
      <ul>
        {bands.map((b) => {
          const s = BAND_STYLE[b];
          return (
            <li key={b}>
              <ShapeIcon shape={s.shape} fill={s.fill} stroke={s.stroke} label={s.range.split("-")[0]} />
              <span>
                {s.label}: score {s.range} ({s.shape})
              </span>
            </li>
          );
        })}
      </ul>
      <p className="disclaimer" role="note" data-testid="disclaimer">
        <strong>Unverified placeholder classifier.</strong> Classifier version:{" "}
        <code data-testid="classifier-version">{classifierVersions.length > 0 ? classifierVersions.join(", ") : "unknown (no loans loaded)"}</code>
        . Every loan is currently treated as a bridge loan with score 100; scores are not a real risk signal.
      </p>
    </section>
  );
}
