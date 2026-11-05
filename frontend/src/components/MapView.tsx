import { geoMercator, geoPath } from "d3-geo";
import { select } from "d3-selection";
import "d3-transition"; // adds .transition() to selections
import { zoom, zoomIdentity } from "d3-zoom";
import type { ZoomBehavior, ZoomTransform } from "d3-zoom";
import type { GeoPermissibleObjects } from "d3-geo";
import { useEffect, useMemo, useRef, useState } from "react";
import { feature } from "topojson-client";
import type { GeometryCollection, Topology } from "topojson-specification";
import topo from "../assets/mattapoisett.topo.json";
import { BAND_STYLE, LAND_COLOR } from "../lib/colors";
import { describeLoan, formatPrincipal } from "../lib/transforms";
import type { LoanRow } from "../lib/types";
import { ShapeIcon } from "./Legend";

const PAN_STEP = 80;
const topology = topo as unknown as Topology; // SAFETY: bundled placeholder file with a known TopoJSON shape
const boundary = feature(topology, topology.objects.mattapoisett as GeometryCollection);

const prefersReducedMotion = (): boolean => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

interface Props {
  rows: LoanRow[]; // mapped rows only
  selectedId: string | null;
  cardOpen: boolean;
  onActivate: (id: string) => void;
}

export function MapView({ rows, selectedId, cardOpen, onActivate }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const zoomRef = useRef<ZoomBehavior<HTMLDivElement, unknown> | null>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [t, setT] = useState<ZoomTransform>(zoomIdentity);
  const [hover, setHover] = useState<string | null>(null);
  const [focus, setFocus] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState<string | null>(null);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    setSize({ w: el.clientWidth, h: el.clientHeight });
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const z = zoom<HTMLDivElement, unknown>()
      .scaleExtent([1, 14])
      .filter((e: Event) => {
        const ev = e as MouseEvent | WheelEvent;
        if (e.type === "wheel") return ev.ctrlKey || ev.metaKey; // plain wheel keeps scrolling the page
        if ((e.target as Element).closest("button")) return false; // pins and controls handle their own events
        return !ev.button;
      })
      .on("zoom", (e: { transform: ZoomTransform }) => setT(e.transform));
    select(el).call(z);
    zoomRef.current = z;
    return () => {
      select(el).on(".zoom", null);
    };
  }, []);

  useEffect(() => {
    zoomRef.current?.translateExtent([
      [0, 0],
      [size.w, size.h],
    ]);
  }, [size]);

  const projection = useMemo(() => {
    if (size.w === 0) return null;
    return geoMercator().fitExtent(
      [
        [24, 24],
        [size.w - 24, size.h - 24],
      ],
      boundary as GeoPermissibleObjects,
    );
  }, [size]);
  const landPath = useMemo(() => (projection ? geoPath(projection)(boundary) : null), [projection]);

  const act = (fn: (z: ZoomBehavior<HTMLDivElement, unknown>, s: ReturnType<typeof select<HTMLDivElement, unknown>>) => void) => {
    const el = box.current;
    const z = zoomRef.current;
    if (el && z) fn(z, select(el));
  };
  const scaleBy = (k: number) =>
    act((z, s) => (prefersReducedMotion() ? s.call(z.scaleBy, k) : s.transition().duration(250).call(z.scaleBy, k)));
  const panBy = (dx: number, dy: number) => act((z, s) => s.call(z.translateBy, dx / t.k, dy / t.k));
  const reset = () => act((z, s) => s.call(z.transform, zoomIdentity));

  useEffect(() => {
    const el = box.current?.parentElement;
    if (!el) return;
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as Element;
      if (target.closest("input, select, textarea")) return;
      const moves: Record<string, [number, number]> = {
        ArrowLeft: [PAN_STEP, 0],
        ArrowRight: [-PAN_STEP, 0],
        ArrowUp: [0, PAN_STEP],
        ArrowDown: [0, -PAN_STEP],
      };
      const move = moves[e.key];
      if (move) {
        e.preventDefault();
        panBy(move[0], move[1]);
      } else if (e.key === "+" || e.key === "=") scaleBy(1.5);
      else if (e.key === "-" || e.key === "_") scaleBy(1 / 1.5);
    };
    el.addEventListener("keydown", onKey);
    return () => el.removeEventListener("keydown", onKey);
  });

  // Esc dismisses the tooltip when no card is open (the app closes the card first).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !cardOpen) setDismissed(hover ?? focus);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [cardOpen, hover, focus]);

  const tipId = hover ?? focus;
  const tipRow = tipId !== null && tipId !== dismissed ? rows.find((r) => r.loanId === tipId) : undefined;
  const place = (r: LoanRow): [number, number] | null => {
    if (!projection || r.lon === null || r.lat === null) return null;
    const p = projection([r.lon, r.lat]);
    return p ? t.apply(p) : null;
  };
  const tipPos = tipRow ? place(tipRow) : null;

  return (
    <div className="map-wrap">
      <div className="map-controls" role="toolbar" aria-label="Map controls">
        <button type="button" onClick={() => scaleBy(1.5)} aria-label="Zoom in">
          +
        </button>
        <button type="button" onClick={() => scaleBy(1 / 1.5)} aria-label="Zoom out">
          &minus;
        </button>
        <button type="button" onClick={() => panBy(PAN_STEP, 0)} aria-label="Pan left">
          &larr;
        </button>
        <button type="button" onClick={() => panBy(0, PAN_STEP)} aria-label="Pan up">
          &uarr;
        </button>
        <button type="button" onClick={() => panBy(0, -PAN_STEP)} aria-label="Pan down">
          &darr;
        </button>
        <button type="button" onClick={() => panBy(-PAN_STEP, 0)} aria-label="Pan right">
          &rarr;
        </button>
        <button type="button" onClick={reset}>
          Reset view
        </button>
      </div>
      <p className="hint" id="map-hint">
        Arrow keys pan, plus and minus zoom (when focus is in the map). Ctrl + scroll zooms. The table below lists every loan.
      </p>
      <div className="map" ref={box} data-testid="map" data-zoom={t.k.toFixed(2)} role="group" aria-label="Map of mapped loans" aria-describedby="map-hint">
        <svg width={size.w} height={size.h} aria-hidden="true" focusable="false">
          <rect width={size.w} height={size.h} fill="#ffffff" />
          <g transform={t.toString()}>
            {landPath ? <path d={landPath} fill={LAND_COLOR} stroke="#5b6770" strokeWidth={1.5 / t.k} /> : null}
          </g>
        </svg>
        {rows.map((r) => {
          const pos = place(r);
          if (!pos) return null;
          const style = BAND_STYLE[r.props.score_band];
          const selected = r.loanId === selectedId;
          return (
            <button
              key={r.loanId}
              type="button"
              className="pin"
              data-loan-id={r.loanId}
              data-band={r.props.score_band}
              aria-label={describeLoan(r)}
              aria-pressed={selected}
              aria-describedby={tipRow?.loanId === r.loanId ? "pin-tooltip" : undefined}
              style={{ left: pos[0], top: pos[1] }}
              onClick={() => onActivate(r.loanId)}
              onMouseEnter={() => {
                setHover(r.loanId);
                setDismissed(null);
              }}
              onMouseLeave={() => setHover(null)}
              onFocus={() => {
                setFocus(r.loanId);
                setDismissed(null);
              }}
              onBlur={() => setFocus(null)}
            >
              <ShapeIcon shape={style.shape} fill={style.fill} stroke={style.stroke} label={String(r.props.bridge_score)} />
            </button>
          );
        })}
        {tipRow && tipPos ? (
          <div role="tooltip" id="pin-tooltip" className="tooltip" style={{ left: tipPos[0], top: tipPos[1] }}>
            <strong>{tipRow.props.address_norm ?? "Address unknown"}</strong>
            <br />
            Lender: {tipRow.props.lender ?? "unknown"}
            <br />
            Principal: {formatPrincipal(tipRow.props.principal)}
            <br />
            Score: {tipRow.props.bridge_score}
          </div>
        ) : null}
      </div>
    </div>
  );
}
