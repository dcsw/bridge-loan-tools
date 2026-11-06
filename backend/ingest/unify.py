"""Silver unification: bronze pages from several extractors -> one ``Instrument`` per instrument.

Design section 3.2 rules, implemented here:

* Each extractor's page text is parsed into raw per-instrument field strings
  (:func:`parse_page`, :func:`segment_pages`). A page that starts with a document heading
  (MORTGAGE / DEED / DISCHARGE) opens a new instrument; headerless pages continue the previous one,
  so an instrument is a *page range*.
* Instruments are aligned across extractors (:func:`align_segments`) by recorded reference
  (book/page), falling back to overlapping page ranges when a reference is missing or garbled.
  Ordinals follow first-page order, so ``instrument_id = <doc_id>-<ordinal>`` is stable.
* Per field, values are normalized with ``backend.common.normalize`` and compared
  (:func:`unify_field`): >= 2 extractors agreeing wins with confidence 0.9; otherwise the
  highest-precedence extractor from :data:`EXTRACTOR_PRECEDENCE` wins with confidence 0.5 and
  ``disagreement=True`` when conflicting non-null values existed. All-null stays null: nothing is
  ever imputed. Agreement is not truth: two extractors agreeing on a wrong value still win.
* An extractor with no usable pages simply contributes no candidates (failure isolation).
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from pydantic import ValidationError

from backend.common.models import BronzeRecord, FieldValue, Instrument
from backend.common.normalize import (
    classify_lender_type,
    normalize_address,
    normalize_lender_name,
    parse_date,
    parse_money,
)

AGREE_CONFIDENCE = 0.9
FALLBACK_CONFIDENCE = 0.5

DOC_TYPES = ("mortgage", "deed", "discharge")
FIELD_NAMES: tuple[str, ...] = (
    "doc_type",
    "grantor",
    "grantee",
    "address_raw",
    "parcel_key",
    "recorded_date",
    "recorded_book",
    "recorded_page",
    "principal",
    "term_months",
    "maturity_date",
    "refers_to_ref",
)

# Precedence when no two extractors agree (first = most trusted). Names and addresses prefer the
# OCR-capable layout-aware extractor (it recovered text the others lost on the scanned fixture);
# structured scalars (money, dates, references) prefer the exact-text PyMuPDF layer.
DEFAULT_PRECEDENCE: tuple[str, ...] = ("pymupdf", "docling", "unstructured")
_NAME_PRECEDENCE: tuple[str, ...] = ("unstructured", "docling", "pymupdf")
EXTRACTOR_PRECEDENCE: dict[str, tuple[str, ...]] = {
    "doc_type": DEFAULT_PRECEDENCE,
    "grantor": _NAME_PRECEDENCE,
    "grantee": _NAME_PRECEDENCE,
    "address_raw": _NAME_PRECEDENCE,
    "parcel_key": DEFAULT_PRECEDENCE,
    "recorded_date": DEFAULT_PRECEDENCE,
    "recorded_book": DEFAULT_PRECEDENCE,
    "recorded_page": DEFAULT_PRECEDENCE,
    "principal": DEFAULT_PRECEDENCE,
    "term_months": DEFAULT_PRECEDENCE,
    "maturity_date": DEFAULT_PRECEDENCE,
    "refers_to_ref": DEFAULT_PRECEDENCE,
}


def normalize_town(town: str) -> str:
    """The one town normalization shared by silver rows and publish coverage."""
    return town.strip().lower()


def precedence(field_name: str) -> tuple[str, ...]:
    return EXTRACTOR_PRECEDENCE.get(field_name, DEFAULT_PRECEDENCE)


@dataclass(frozen=True)
class DocMeta:
    """Manifest facts silver needs per document (WU4 manifest row: town, source_url)."""

    town: str
    source_url: str | None


@dataclass(frozen=True)
class Skipped:
    doc_id: str
    reason: str
    detail: str = ""


@dataclass(frozen=True)
class UnifiedInstrument:
    instrument: Instrument
    lender_norm: str | None
    text: str  # page text of the best extractor for this instrument (keyword signal input)
    ingested_at: datetime  # max bronze extracted_at of the document: deterministic across reruns


@dataclass
class UnifyResult:
    instruments: list[UnifiedInstrument] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)


# -- parsing ------------------------------------------------------------------------------

_MARKER = re.compile(
    r"\b(Grantor|Grantee|Property|Parcel|Recorded|Principal|Term|Maturity)\s*:"
    r"|\b(Discharges mortgage recorded at)\b"
    r"|\b(SYNTHETIC TEST DOCUMENT)\b"
)
_LABEL_FIELD = {
    "grantor": "grantor",
    "grantee": "grantee",
    "property": "address_raw",
    "parcel": "parcel_key",
    "principal": "principal",
    "term": "term_months",
    "maturity": "maturity_date",
    "discharges mortgage recorded at": "refers_to_ref",
}


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _heading_type(zone: str) -> str | None:
    upper = zone.upper()
    if "DISCHARGE" in upper:
        return "discharge"
    if "MORTGAGE" in upper:
        return "mortgage"
    if "DEED" in upper:
        return "deed"
    return None


def parse_page(text: str) -> dict[str, str | None] | None:
    """Raw (un-normalized) field strings from one page; None when nothing is recognizable."""
    markers = list(_MARKER.finditer(text))
    zone = _collapse(text[: markers[0].start()] if markers else text)
    raw: dict[str, str | None] = dict.fromkeys(FIELD_NAMES)
    raw["doc_type"] = _heading_type(zone)
    for i, m in enumerate(markers):
        label = (m.group(1) or m.group(2) or m.group(3)).lower()
        if label == "synthetic test document":
            break
        end = markers[i + 1].start() if i + 1 < len(markers) else len(text)
        value = _collapse(text[m.end() : end])
        if label == "recorded":
            date_part = re.split(r"\bBook\b", value, maxsplit=1)[0].strip()
            raw["recorded_date"] = date_part or None
            book = re.search(r"\bBook\s+(\S+)", value)
            page = re.search(r"\bPage\s+(\S+)", value)
            raw["recorded_book"] = book.group(1) if book else None
            raw["recorded_page"] = page.group(1) if page else None
        else:
            raw[_LABEL_FIELD[label]] = value or None
    if all(v is None for v in raw.values()):
        return None
    return raw


@dataclass
class Segment:
    """One extractor's view of one instrument: a page range plus raw field strings."""

    extractor: str
    page_start: int
    page_end: int
    raw: dict[str, str | None]
    text: str

    @property
    def ref(self) -> str | None:
        book, page = self.raw.get("recorded_book"), self.raw.get("recorded_page")
        return f"{book.strip()}/{page.strip()}" if book and page else None


def segment_pages(extractor: str, records: Sequence[BronzeRecord]) -> list[Segment]:
    """Split one extractor's pages of one document into instrument segments."""
    segments: list[Segment] = []
    for rec in sorted(records, key=lambda r: r.page_no):
        if rec.status == "failed" or not rec.text.strip():
            continue
        parsed = parse_page(rec.text)
        if parsed is None:
            continue
        if parsed["doc_type"] is not None:
            segments.append(Segment(extractor, rec.page_no, rec.page_no, parsed, rec.text))
        elif segments:
            current = segments[-1]
            current.page_end = rec.page_no
            current.text = f"{current.text}\n{rec.text}"
            for key, value in parsed.items():
                if current.raw.get(key) is None and value is not None:
                    current.raw[key] = value
    return segments


def _overlaps(a: list[Segment], b: list[Segment]) -> bool:
    a_lo, a_hi = min(s.page_start for s in a), max(s.page_end for s in a)
    b_lo, b_hi = min(s.page_start for s in b), max(s.page_end for s in b)
    return a_lo <= b_hi and b_lo <= a_hi


def align_segments(segments: Sequence[Segment]) -> list[list[Segment]]:
    """Group segments of different extractors that describe the same instrument.

    First by recorded reference, then groups that share no extractor but cover overlapping pages
    are merged (a missing or garbled reference must not split an instrument). Two instruments on
    one physical page are not supported. Groups are returned in first-page order.
    """
    groups: list[list[Segment]] = []
    for seg in sorted(segments, key=lambda s: (s.page_start, s.page_end, s.extractor)):
        target = next(
            (
                g
                for g in groups
                if seg.ref is not None
                and any(s.ref == seg.ref for s in g)
                and all(s.extractor != seg.extractor for s in g)
            ),
            None,
        )
        if target is None:
            groups.append([seg])
        else:
            target.append(seg)
    merged = True
    while merged:
        merged = False
        for i, a in enumerate(groups):
            for j in range(i + 1, len(groups)):
                b = groups[j]
                if {s.extractor for s in a}.isdisjoint(s.extractor for s in b) and _overlaps(a, b):
                    a.extend(groups.pop(j))
                    merged = True
                    break
            if merged:
                break
    groups.sort(key=lambda g: (min(s.page_start for s in g), min(s.page_end for s in g)))
    return groups


# -- field normalization and the agreement rule -------------------------------------------


def _norm(field_name: str, raw: object) -> tuple[str, object] | None:
    """(comparison key, JSON-safe stored value) or None when null/unparseable."""
    if raw is None:
        return None
    text = _collapse(str(raw))
    if not text:
        return None
    if field_name == "doc_type":
        key = text.lower()
        return (key, key) if key in DOC_TYPES else None
    if field_name in ("grantor", "grantee"):
        key = normalize_lender_name(text)
        return (key, text) if key else None
    if field_name == "address_raw":
        key = normalize_address(text)
        return (key, text) if key else None
    if field_name == "parcel_key":
        return text.upper(), text.upper()
    if field_name == "principal":
        money = parse_money(text)
        if money is None or money < 0:
            return None
        key = f"{money:.2f}"
        return key, key
    if field_name in ("recorded_date", "maturity_date"):
        parsed = parse_date(text)
        return (parsed.isoformat(), parsed.isoformat()) if parsed else None
    if field_name == "term_months":
        m = re.match(r"\d+", text)
        return (str(int(m.group())), int(m.group())) if m else None
    if field_name == "refers_to_ref":
        ref = re.sub(r"\s*/\s*", "/", text)
        return ref, ref
    return text, text  # recorded_book / recorded_page


def canonical(field_name: str, raw: object) -> str | None:
    """Comparison key used for cross-extractor agreement; None when null or unparseable."""
    normed = _norm(field_name, raw)
    return None if normed is None else normed[0]


def unify_field(field_name: str, candidates: Mapping[str, object]) -> FieldValue:
    order = precedence(field_name)

    def rank(extractor: str) -> int:
        return order.index(extractor) if extractor in order else len(order)

    found = {e: n for e, raw in candidates.items() if (n := _norm(field_name, raw)) is not None}
    if not found:
        return FieldValue(value=None, source_extractor=None, confidence=0.0, disagreement=False)
    groups: dict[str, list[str]] = defaultdict(list)
    for extractor, (key, _) in found.items():
        groups[key].append(extractor)
    agreeing = [members for members in groups.values() if len(members) >= 2]
    if agreeing:
        best = min(agreeing, key=lambda members: min(rank(e) for e in members))
        source = min(best, key=rank)
        return FieldValue(
            value=found[source][1],
            source_extractor=source,
            confidence=AGREE_CONFIDENCE,
            disagreement=False,
        )
    source = min(found, key=rank)
    return FieldValue(
        value=found[source][1],
        source_extractor=source,
        confidence=FALLBACK_CONFIDENCE,
        disagreement=len(found) > 1,
    )


# -- documents ----------------------------------------------------------------------------


def _decimal(fv: FieldValue) -> Decimal | None:
    return None if fv.value is None else Decimal(str(fv.value))


def _date(fv: FieldValue) -> date | None:
    return None if fv.value is None else date.fromisoformat(str(fv.value))


def _unify_group(
    group: list[Segment], doc_id: str, ordinal: int, meta: DocMeta, ingested_at: datetime
) -> UnifiedInstrument:
    fields = {
        name: unify_field(name, {s.extractor: s.raw.get(name) for s in group})
        for name in FIELD_NAMES
    }
    v = {name: fv.value for name, fv in fields.items()}
    doc_type = v["doc_type"]
    address_raw = v["address_raw"]
    lender_norm = normalize_lender_name(v["grantee"]) if v["grantee"] else None
    best = min(
        group,
        key=lambda s: (
            DEFAULT_PRECEDENCE.index(s.extractor)
            if s.extractor in DEFAULT_PRECEDENCE
            else len(DEFAULT_PRECEDENCE)
        ),
    )
    instrument = Instrument(
        instrument_id=f"{doc_id}-{ordinal}",
        doc_id=doc_id,
        ordinal=ordinal,
        doc_type=doc_type,
        town=normalize_town(meta.town),
        parcel_key=v["parcel_key"],
        address_raw=address_raw,
        address_norm=normalize_address(address_raw),
        grantor=v["grantor"],
        grantee=v["grantee"],
        lender_type=classify_lender_type(lender_norm) if doc_type == "mortgage" else None,
        principal=_decimal(fields["principal"]),
        recorded_date=_date(fields["recorded_date"]),
        maturity_date=_date(fields["maturity_date"]),
        term_months=v["term_months"],
        recorded_book=v["recorded_book"],
        recorded_page=v["recorded_page"],
        refers_to_ref=v["refers_to_ref"],
        source_url=meta.source_url,
        fields=fields,
    )
    return UnifiedInstrument(instrument, lender_norm, best.text, ingested_at)


def unify_document(
    doc_id: str, records: Sequence[BronzeRecord], meta: DocMeta | None
) -> tuple[list[UnifiedInstrument], list[Skipped]]:
    if meta is None or not meta.town.strip():
        return [], [Skipped(doc_id, "missing_manifest_meta", "no manifest row with a town")]
    by_extractor: dict[str, list[BronzeRecord]] = defaultdict(list)
    for rec in records:
        by_extractor[rec.extractor].append(rec)
    segments = [
        seg
        for extractor in sorted(by_extractor)
        for seg in segment_pages(extractor, by_extractor[extractor])
    ]
    ingested_at = max(r.extracted_at for r in records)
    out: list[UnifiedInstrument] = []
    skipped: list[Skipped] = []
    for ordinal, group in enumerate(align_segments(segments)):
        try:
            out.append(_unify_group(group, doc_id, ordinal, meta, ingested_at))
        except ValidationError as exc:
            skipped.append(Skipped(f"{doc_id}-{ordinal}", "invalid_instrument", str(exc)[:500]))
    return out, skipped


def unify_records(
    records: Sequence[BronzeRecord],
    meta: Mapping[str, DocMeta] | Callable[[str], DocMeta | None],
) -> UnifyResult:
    """Unify bronze records of any number of documents/extractors."""
    lookup = meta.get if isinstance(meta, Mapping) else meta
    by_doc: dict[str, list[BronzeRecord]] = defaultdict(list)
    for rec in records:
        by_doc[rec.doc_id].append(rec)
    result = UnifyResult()
    for doc_id in sorted(by_doc):
        instruments, skipped = unify_document(doc_id, by_doc[doc_id], lookup(doc_id))
        result.instruments += instruments
        result.skipped += skipped
    return result
