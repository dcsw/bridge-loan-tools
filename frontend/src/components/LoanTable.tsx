import { BAND_STYLE } from "../lib/colors";
import { formatDate, formatPrincipal, sortRows } from "../lib/transforms";
import type { LoanRow, SortDir, SortKey } from "../lib/types";

const COLUMNS: { key: SortKey; label: string }[] = [
  { key: "address", label: "Address" },
  { key: "lender", label: "Lender" },
  { key: "principal", label: "Principal" },
  { key: "term", label: "Term (months)" },
  { key: "recorded", label: "Recorded" },
  { key: "maturity", label: "Maturity" },
  { key: "score", label: "Score" },
];

interface Props {
  rows: LoanRow[];
  sort: { key: SortKey; dir: SortDir };
  selectedId: string | null;
  view: "all" | "unmapped";
  onSort: (key: SortKey) => void;
  onSelect: (id: string) => void;
  onDetails: (id: string) => void;
  onShowAll: () => void;
}

export function LoanTable({ rows, sort, selectedId, view, onSort, onSelect, onDetails, onShowAll }: Props) {
  const shown = sortRows(view === "unmapped" ? rows.filter((r) => !r.mapped) : rows, sort.key, sort.dir);
  return (
    <section id="loan-table" className="table-wrap" aria-labelledby="table-title">
      <h2 id="table-title">
        Loans ({shown.length}
        {view === "unmapped" ? " not mapped" : ""})
      </h2>
      {view === "unmapped" ? (
        <p>
          Showing only loans without a map location.{" "}
          <button type="button" className="link" onClick={onShowAll}>
            Show all loans
          </button>
        </p>
      ) : null}
      <table>
        <caption className="sr-only">Loans, sortable by column. Sorted by {sort.key} {sort.dir === "asc" ? "ascending" : "descending"}.</caption>
        <thead>
          <tr>
            {COLUMNS.map((c) => (
              <th key={c.key} scope="col" aria-sort={sort.key === c.key ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}>
                <button type="button" className="sort" onClick={() => onSort(c.key)}>
                  {c.label}
                  <span aria-hidden="true">{sort.key === c.key ? (sort.dir === "asc" ? " ▲" : " ▼") : ""}</span>
                </button>
              </th>
            ))}
            <th scope="col">Details</th>
          </tr>
        </thead>
        <tbody>
          {shown.map((r) => {
            const p = r.props;
            return (
              <tr key={r.loanId} data-selected={r.loanId === selectedId}>
                <th scope="row">
                  <button type="button" className="row-select" data-loan-id={r.loanId} aria-current={r.loanId === selectedId ? "true" : undefined} onClick={() => onSelect(r.loanId)}>
                    {p.address_norm ?? "Address unknown"}
                  </button>
                  {r.mapped ? null : <span className="badge-unmapped"> Not mapped</span>}
                </th>
                <td>{p.lender ?? "unknown"}</td>
                <td>{formatPrincipal(p.principal)}</td>
                <td>{p.term_months ?? "unknown"}</td>
                <td>{formatDate(p.recorded_date)}</td>
                <td>{formatDate(p.maturity_date)}</td>
                <td>
                  {p.bridge_score} <span className="band-text">{BAND_STYLE[p.score_band].label}</span>
                </td>
                <td>
                  <button type="button" onClick={() => onDetails(r.loanId)} aria-label={`Details for ${p.address_norm ?? "address unknown"}`}>
                    Details
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}
