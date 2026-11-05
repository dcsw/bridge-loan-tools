import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useApi } from "./api/useApi";
import { CoverageBanner } from "./components/CoverageBanner";
import { Filters } from "./components/Filters";
import { Legend } from "./components/Legend";
import { LoanCard } from "./components/LoanCard";
import { LoanTable } from "./components/LoanTable";
import { MapView } from "./components/MapView";
import { RetryNotice } from "./components/RetryNotice";
import { splitFeatures } from "./lib/transforms";
import type { FeatureCollection, SortKey } from "./lib/types";
import { DEFAULT_URL_STATE, parseUrlState, serializeUrlState, toApiQuery } from "./lib/url-state";
import type { UrlState } from "./lib/url-state";

function useUrlState(): [UrlState, (fn: (s: UrlState) => UrlState) => void] {
  const [state, setState] = useState<UrlState>(() => parseUrlState(window.location.search));
  const ref = useRef(state);
  const update = useCallback((fn: (s: UrlState) => UrlState) => {
    const next = fn(ref.current);
    ref.current = next;
    setState(next);
    window.history.replaceState(null, "", `${window.location.pathname}${serializeUrlState(next)}${window.location.hash}`);
  }, []);
  useEffect(() => {
    const onPop = () => {
      ref.current = parseUrlState(window.location.search);
      setState(ref.current);
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  return [state, update];
}

function focusLoan(selector: string, id: string): void {
  document.querySelector<HTMLElement>(`${selector}[data-loan-id="${CSS.escape(id)}"]`)?.focus();
}

export function App() {
  const [url, update] = useUrlState();
  const [cardOpen, setCardOpen] = useState(url.selected !== null);
  const [focusReq, setFocusReq] = useState<{ n: number; target: "pin" | "row" | "card"; id: string } | null>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);

  const [list, reloadList] = useApi<FeatureCollection>(`/loans?${toApiQuery(url.filters)}`);
  const loaded = list.status === "ok" ? list.data : null;
  const { rows } = useMemo(() => splitFeatures(loaded?.features ?? []), [loaded]);
  const mapped = useMemo(() => rows.filter((r) => r.mapped), [rows]);
  const versions = useMemo(() => [...new Set(rows.map((r) => r.props.classifier_version))], [rows]);
  const selectedRow = rows.find((r) => r.loanId === url.selected) ?? null;
  const showCard = cardOpen && selectedRow !== null;

  const request = (target: "pin" | "row" | "card", id: string) => setFocusReq((p) => ({ n: (p?.n ?? 0) + 1, target, id }));

  useEffect(() => {
    if (!focusReq) return;
    if (focusReq.target === "card") headingRef.current?.focus();
    else focusLoan(focusReq.target === "pin" ? "button.pin" : "button.row-select", focusReq.id);
  }, [focusReq]);

  const openCard = (id: string) => {
    update((s) => ({ ...s, selected: id }));
    setCardOpen(true);
    request("card", id);
  };
  const closeCard = () => {
    const row = selectedRow;
    setCardOpen(false);
    if (row) request(row.mapped ? "pin" : "row", row.loanId);
  };
  const selectFromTable = (id: string) => {
    update((s) => ({ ...s, selected: id }));
    const row = rows.find((r) => r.loanId === id);
    if (row?.mapped) request("pin", id);
    else openCard(id);
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && showCard) closeCard();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  });

  const filtersActive = Object.values(url.filters).some((v) => v !== null);
  const clearFilters = () => update((s) => ({ ...s, filters: DEFAULT_URL_STATE.filters, view: "all" }));
  const toggleSort = (key: SortKey) =>
    update((s) => ({ ...s, sort: { key, dir: s.sort.key === key ? (s.sort.dir === "asc" ? "desc" : "asc") : key === "score" ? "desc" : "asc" } }));

  return (
    <>
      <a className="skip" href="#loan-table">
        Skip to loan table
      </a>
      <header>
        <h1>Bridge loan map</h1>
        <p className="disclaimer-top" role="note">
          Unverified placeholder classifier: every loan is currently flagged as a bridge loan. Do not rely on scores.
        </p>
      </header>
      <CoverageBanner />
      <Filters
        filters={url.filters}
        onChange={(patch) => update((s) => ({ ...s, filters: { ...s.filters, ...patch } }))}
        onClear={clearFilters}
      />
      <main aria-busy={list.status === "loading"}>
        <div aria-live="polite" className="status" data-testid="status">
          {list.status === "loading" ? "Loading loans..." : null}
          {loaded ? `${loaded.total} loans match${loaded.next_cursor !== null ? "; showing the first " + loaded.features.length : ""}.` : null}
        </div>
        {list.status === "error" ? <RetryNotice what="loans" error={list.error} attempts={list.attempts} onRetry={reloadList} /> : null}
        {loaded && loaded.next_cursor !== null ? (
          <div className="notice notice-warn" role="status" data-testid="partial">
            Partial results: showing {loaded.features.length} of {loaded.total} loans. Narrow the filters to see the rest.
          </div>
        ) : null}
        {loaded && loaded.unmapped_count > 0 ? (
          <p className="unmapped">
            <a
              href="?view=unmapped#loan-table"
              onClick={(e) => {
                e.preventDefault();
                update((s) => ({ ...s, view: "unmapped" }));
                document.getElementById("loan-table")?.scrollIntoView();
              }}
            >
              {loaded.unmapped_count} not mapped
            </a>{" "}
            (no location found; listed in the table)
          </p>
        ) : null}
        {loaded && rows.length === 0 ? (
          <div className="notice" data-testid="empty">
            <p>No loans match these filters.</p>
            {filtersActive ? (
              <button type="button" onClick={clearFilters}>
                Clear filters
              </button>
            ) : null}
          </div>
        ) : null}
        <div className="layout">
          <MapView rows={mapped} selectedId={url.selected} cardOpen={showCard} onActivate={openCard} />
          {showCard && selectedRow ? <LoanCard key={selectedRow.loanId} ref={headingRef} row={selectedRow} onClose={closeCard} /> : null}
        </div>
        <Legend classifierVersions={versions} />
        {loaded && rows.length > 0 ? (
          <LoanTable
            rows={rows}
            sort={url.sort}
            selectedId={url.selected}
            view={url.view}
            onSort={toggleSort}
            onSelect={selectFromTable}
            onDetails={openCard}
            onShowAll={() => update((s) => ({ ...s, view: "all" }))}
          />
        ) : null}
      </main>
    </>
  );
}
