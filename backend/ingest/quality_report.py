"""Quality report (Phase 2 exit artifact): cross-extractor agreement, null rates, extractor
comparison and coverage with gaps, per town. No Spark, no network: it reads the extractors' JSON
(``data/extract-out/<extractor>/*.json``) and the WU4 manifest rows (``data/manifests``).

    python -m backend.ingest.quality_report --extract-out data/extract-out \
        --manifests data/manifests --out docs/reports/quality-report

Definitions (per instrument, per field, over the extractors that produced that instrument):

* ``all_null``   no extractor produced a usable value
* ``single``     exactly one extractor produced a value (nothing to compare)
* ``unanimous``  >= 2 produced values and every one normalizes to the same key
* ``majority``   >= 2 agree but at least one other value differs
* ``conflict``   >= 2 produced values, no two agree

``agreement_rate = (unanimous + majority) / comparable`` where comparable = instruments with
>= 2 values. **Agreement is not accuracy**: extractors can agree on a wrong value, and no labels
exist (design section 9). Thresholds are set after the first real baseline.

Null rates are not conditioned on document type: ``refers_to_ref`` only exists on discharges and
``maturity_date`` is often absent on mortgages, so a high null rate there is expected. ``doc_type``
is near-vacuous by construction (an instrument only exists where a heading was found).
``null_rate`` counts only instruments an extractor saw; ``null_or_missed_rate`` also counts
instruments it found no segment for. ``consensus_match_rate`` is over cases where the extractor
produced a value.

The report holds only counts and rates: no names, addresses, amounts or document text.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.common.models import BronzeRecord, Manifest
from backend.ingest.quarantine import load_extractor_dir
from backend.ingest.unify import (
    FIELD_NAMES,
    DocMeta,
    align_segments,
    canonical,
    normalize_town,
    segment_pages,
)

PHASE2_DOC_TARGET = 100
EXTRACTORS: tuple[str, ...] = ("docling", "pymupdf", "unstructured")
CATEGORIES = ("all_null", "single", "unanimous", "majority", "conflict")


def _rate(num: int, den: int) -> float | None:
    return None if den == 0 else round(num / den, 4)


@dataclass
class FieldAgg:
    cats: Counter[str] = field(default_factory=Counter)
    # per extractor: segments seen, null values, consensus cases present, consensus matches
    seen: Counter[str] = field(default_factory=Counter)
    null: Counter[str] = field(default_factory=Counter)
    missed: Counter[str] = field(default_factory=Counter)  # extractor had the doc, no segment
    consensus_cases: Counter[str] = field(default_factory=Counter)
    consensus_match: Counter[str] = field(default_factory=Counter)


@dataclass
class TownAgg:
    documents: set[str] = field(default_factory=set)
    docs_without_instruments: set[str] = field(default_factory=set)
    instruments: int = 0
    doc_types: Counter[str] = field(default_factory=Counter)
    extractors_per_instrument: Counter[int] = field(default_factory=Counter)
    page_status: dict[str, Counter[str]] = field(
        default_factory=lambda: defaultdict(Counter)
    )  # extractor -> {ok, partial, failed, partial:needs_ocr}
    fields: dict[str, FieldAgg] = field(default_factory=lambda: defaultdict(FieldAgg))


def _categorize(keys: Mapping[str, str | None]) -> tuple[str, str | None]:
    """(category, consensus key or None) for one field of one instrument."""
    present = {e: k for e, k in keys.items() if k is not None}
    if not present:
        return "all_null", None
    if len(present) == 1:
        return "single", None
    counts = Counter(present.values())
    top_key, top_n = counts.most_common(1)[0]
    if top_n == len(present):
        return "unanimous", top_key
    if top_n >= 2 and list(counts.values()).count(top_n) == 1:
        return "majority", top_key
    if top_n >= 2:  # two different pairs: no single consensus
        return "majority", None
    return "conflict", None


def aggregate(records: Sequence[BronzeRecord], meta: Mapping[str, DocMeta]) -> dict[str, TownAgg]:
    by_doc: dict[str, list[BronzeRecord]] = defaultdict(list)
    for rec in records:
        by_doc[rec.doc_id].append(rec)
    towns: dict[str, TownAgg] = defaultdict(TownAgg)
    for doc_id in sorted(by_doc):
        info = meta.get(doc_id)
        town = normalize_town(info.town) if info and info.town.strip() else "unknown"
        agg = towns[town]
        agg.documents.add(doc_id)
        by_extractor: dict[str, list[BronzeRecord]] = defaultdict(list)
        for rec in by_doc[doc_id]:
            by_extractor[rec.extractor].append(rec)
            status = "partial:needs_ocr" if rec.status == "partial" and rec.error else rec.status
            agg.page_status[rec.extractor][status] += 1
        segments = [
            seg
            for extractor in sorted(by_extractor)
            for seg in segment_pages(extractor, by_extractor[extractor])
        ]
        groups = align_segments(segments)
        if not groups:
            agg.docs_without_instruments.add(doc_id)
        doc_extractors = set(by_extractor)
        for group in groups:
            agg.instruments += 1
            absent = doc_extractors - {s.extractor for s in group}
            agg.extractors_per_instrument[len({s.extractor for s in group})] += 1
            for name in FIELD_NAMES:
                keys = {s.extractor: canonical(name, s.raw.get(name)) for s in group}
                if name == "doc_type":
                    agg.doc_types[next((k for k in keys.values() if k), "unknown")] += 1
                cat, consensus = _categorize(keys)
                fa = agg.fields[name]
                fa.cats[cat] += 1
                for extractor in absent:
                    fa.missed[extractor] += 1
                for extractor, key in keys.items():
                    fa.seen[extractor] += 1
                    if key is None:
                        fa.null[extractor] += 1
                    if consensus is not None and key is not None:
                        fa.consensus_cases[extractor] += 1
                        fa.consensus_match[extractor] += key == consensus
    return towns


def _field_report(fa: FieldAgg) -> dict[str, Any]:
    total = sum(fa.cats.values())
    comparable = fa.cats["unanimous"] + fa.cats["majority"] + fa.cats["conflict"]
    return {
        "instruments": total,
        "comparable": comparable,
        **{c: fa.cats[c] for c in CATEGORIES},
        "null_rate": _rate(fa.cats["all_null"], total),
        "agreement_rate": _rate(fa.cats["unanimous"] + fa.cats["majority"], comparable),
        "per_extractor": {
            e: {
                "seen": fa.seen[e],
                "missed_instruments": fa.missed[e],
                "null_rate": _rate(fa.null[e], fa.seen[e]),
                "null_or_missed_rate": _rate(fa.null[e] + fa.missed[e], fa.seen[e] + fa.missed[e]),
                "consensus_match_rate": _rate(fa.consensus_match[e], fa.consensus_cases[e]),
            }
            for e in sorted(set(fa.seen) | set(fa.missed))
        },
    }


def coverage(manifests: Sequence[Manifest], extracted: set[str]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in manifests:
        town = normalize_town(row.town) if row.town.strip() else "unknown"
        c = out.setdefault(
            town,
            {"fetched": 0, "gaps": Counter(), "fetched_not_extracted": 0, "last_fetched_at": None},
        )
        if row.status == "fetched":
            c["fetched"] += 1
            c["fetched_not_extracted"] += row.doc_id not in extracted
            ts = row.fetched_at.astimezone(UTC).isoformat()
            c["last_fetched_at"] = max(filter(None, [c["last_fetched_at"], ts]))
        else:
            c["gaps"][row.gap_reason or "unknown"] += 1
    for c in out.values():
        c["gaps"] = dict(sorted(c["gaps"].items()))
    return out


def build_report(
    records: Sequence[BronzeRecord],
    manifests: Sequence[Manifest],
    *,
    quarantined: Mapping[str, int] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    meta = {
        r.doc_id: DocMeta(town=r.town, source_url=r.source_url)
        for r in manifests
        if r.status == "fetched"
    }
    towns = aggregate(records, meta)
    extracted = {r.doc_id for r in records}
    cov = coverage(manifests, extracted)
    report_towns: dict[str, Any] = {}
    for town, agg in sorted(towns.items()):
        report_towns[town] = {
            "documents_extracted": len(agg.documents),
            "documents_without_instruments": len(agg.docs_without_instruments),
            "instruments": agg.instruments,
            "doc_types": dict(sorted(agg.doc_types.items())),
            "extractors_per_instrument": {
                str(k): v for k, v in sorted(agg.extractors_per_instrument.items())
            },
            "page_status": {e: dict(sorted(c.items())) for e, c in sorted(agg.page_status.items())},
            "fields": {name: _field_report(agg.fields[name]) for name in FIELD_NAMES},
            "coverage": cov.get(town),
        }
    # Documents without a manifest town are skipped by silver, so they do not count to the target.
    total_docs = sum(len(a.documents) for t, a in towns.items() if t != "unknown")
    return {
        "generated_at": (now or datetime.now(UTC)).isoformat(timespec="seconds"),
        "note": (
            "Agreement is not accuracy; no labeled ground truth exists (design section 9). "
            "Counts and rates only; no document content."
        ),
        "phase2_exit": {
            "documents_extracted": total_docs,
            "target": PHASE2_DOC_TARGET,
            "target_met": total_docs >= PHASE2_DOC_TARGET,
        },
        "quarantined_records": dict(quarantined or {}),
        "towns": report_towns,
        "coverage_towns_without_extracted_docs": sorted(set(cov) - set(towns)),
    }


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def render_markdown(report: Mapping[str, Any]) -> str:
    exit_ = report["phase2_exit"]
    lines = [
        "# Quality report",
        "",
        f"Generated {report['generated_at']}. {report['note']}",
        "",
        f"**Phase 2 document target:** {exit_['documents_extracted']} / {exit_['target']} "
        f"extracted ({'met' if exit_['target_met'] else 'NOT met'}).",
        "",
    ]
    if report["quarantined_records"]:
        q = ", ".join(f"{e}: {n}" for e, n in report["quarantined_records"].items())
        lines += [f"Quarantined records by extractor: {q}.", ""]
    for town, t in report["towns"].items():
        lines += [
            f"## {town}",
            "",
            f"{t['documents_extracted']} documents extracted, {t['instruments']} instruments, "
            f"{t['documents_without_instruments']} documents with no parseable instrument. "
            f"Document types: {t['doc_types'] or 'none'}.",
            "",
        ]
        cov = t["coverage"]
        if cov:
            gaps = ", ".join(f"{k}: {v}" for k, v in cov["gaps"].items()) or "none"
            lines += [
                f"Coverage: {cov['fetched']} fetched ({cov['fetched_not_extracted']} not yet "
                f"extracted); gaps: {gaps}; last fetched {cov['last_fetched_at']}.",
                "",
            ]
        lines += [
            "| Field | Instruments | Comparable | Null rate | Agreement | Unanimous | Majority "
            "| Conflict "
            "| Single |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for name, f in t["fields"].items():
            lines.append(
                f"| {name} | {f['instruments']} | {f['comparable']} | {_pct(f['null_rate'])} | "
                f"{_pct(f['agreement_rate'])} | {f['unanimous']} | {f['majority']} | "
                f"{f['conflict']} | {f['single']} |"
            )
        lines += [
            "",
            "Extractor comparison (null-or-missed rate / consensus match when it had a value):",
            "",
        ]
        extractors = sorted({e for f in t["fields"].values() for e in f["per_extractor"]})
        lines += ["| Field | " + " | ".join(extractors) + " |", "|---|" + "---|" * len(extractors)]
        for name, f in t["fields"].items():
            cells = []
            for e in extractors:
                pe = f["per_extractor"].get(e)
                cells.append(
                    "n/a"
                    if pe is None
                    else f"{_pct(pe['null_or_missed_rate'])} / {_pct(pe['consensus_match_rate'])}"
                )
            lines.append(f"| {name} | " + " | ".join(cells) + " |")
        lines += ["", "Page status by extractor: " + json.dumps(t["page_status"]), ""]
    if report["coverage_towns_without_extracted_docs"]:
        lines += [
            "Towns with manifest rows but no extracted documents: "
            + ", ".join(report["coverage_towns_without_extracted_docs"]),
            "",
        ]
    return "\n".join(lines)


def load_inputs(
    extract_out: Path, manifests_dir: Path
) -> tuple[list[BronzeRecord], list[Manifest], dict[str, int]]:
    from backend.ingest.silver_job import read_manifest_dir

    records: list[BronzeRecord] = []
    quarantined: dict[str, int] = {}
    for extractor in EXTRACTORS:
        try:
            loaded = load_extractor_dir(extractor, extract_out / extractor)
        except FileNotFoundError:
            continue
        records += loaded.records
        if loaded.rejects:
            quarantined[extractor] = len(loaded.rejects)
    return records, read_manifest_dir(manifests_dir), quarantined


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--extract-out", type=Path, default=Path("data/extract-out"))
    parser.add_argument("--manifests", type=Path, default=Path("data/manifests"))
    parser.add_argument("--out", type=Path, help="write <out>.json and <out>.md")
    args = parser.parse_args(argv)
    records, manifests, quarantined = load_inputs(args.extract_out, args.manifests)
    if not records:
        print(f"warning: no extractor records found under {args.extract_out}", file=sys.stderr)
    report = build_report(records, manifests, quarantined=quarantined)
    markdown = render_markdown(report)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n")
        args.out.with_suffix(".md").write_text(markdown + "\n")
    print(markdown)
    return 0


if __name__ == "__main__":
    sys.exit(main())
