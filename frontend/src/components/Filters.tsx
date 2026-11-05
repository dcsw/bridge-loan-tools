import { LENDER_TYPES, TOWNS } from "../lib/url-state";
import type { Filters as FilterState } from "../lib/url-state";
import type { LenderType, Town } from "../lib/types";

interface Props {
  filters: FilterState;
  onChange: (patch: Partial<FilterState>) => void;
  onClear: () => void;
}

export function Filters({ filters, onChange, onClear }: Props) {
  return (
    <form className="filters" aria-label="Filters" onSubmit={(e) => e.preventDefault()}>
      <label>
        Town
        <select value={filters.town ?? ""} onChange={(e) => onChange({ town: (e.target.value || null) as Town | null })}>
          <option value="">All towns</option>
          {TOWNS.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
      </label>
      <label>
        Min score
        <input
          type="number"
          min={0}
          max={100}
          step={1}
          inputMode="numeric"
          value={filters.minScore ?? ""}
          onChange={(e) => {
            const n = e.target.value === "" ? null : Number(e.target.value);
            onChange({ minScore: n !== null && Number.isInteger(n) && n >= 0 && n <= 100 ? n : null });
          }}
        />
      </label>
      <label>
        Lender type
        <select
          value={filters.lenderType ?? ""}
          onChange={(e) => onChange({ lenderType: (e.target.value || null) as LenderType | null })}
        >
          <option value="">Any</option>
          {LENDER_TYPES.map((t) => (
            <option key={t} value={t}>
              {t.replace("_", " ")}
            </option>
          ))}
        </select>
      </label>
      <label>
        Recorded from
        <input type="date" value={filters.recordedFrom ?? ""} onChange={(e) => onChange({ recordedFrom: e.target.value || null })} />
      </label>
      <label>
        Recorded to
        <input type="date" value={filters.recordedTo ?? ""} onChange={(e) => onChange({ recordedTo: e.target.value || null })} />
      </label>
      <button type="button" onClick={onClear}>
        Clear filters
      </button>
    </form>
  );
}
