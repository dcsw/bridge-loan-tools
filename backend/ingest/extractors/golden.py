"""Tolerance-based comparison of extractor output against committed goldens.

Extractor output is not byte-stable across library/model versions, so goldens are compared
loosely:

* **Text**: whitespace/case-normalized ``difflib`` similarity per page must be
  ``>= TEXT_SIMILARITY_THRESHOLD`` (0.90). 0.90 tolerates reflow, hyphenation and minor OCR
  drift on these short synthetic pages but fails on wrong or missing content.
* **Key fields** (exact): doc_id, extractor, extractor_version, page_no, status, and the
  error *code* (text before the first ``:``).
* ``extracted_at`` and ``tables_json`` are not compared (timestamp; table layout is
  too extractor-specific). ``missing_key_fields`` separately checks that the ground-truth
  values from ``tests/fixtures/truth`` appear in the extracted text.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Any

TEXT_SIMILARITY_THRESHOLD = 0.90
EXACT_FIELDS = ("doc_id", "extractor", "extractor_version", "page_no", "status")


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def text_similarity(a: str, b: str) -> float:
    na, nb = normalize_text(a), normalize_text(b)
    if na == nb:
        return 1.0
    return SequenceMatcher(None, na, nb, autojunk=False).ratio()


def _error_code(error: str | None) -> str | None:
    return None if error is None else error.split(":", 1)[0]


def compare_to_golden(
    actual: Sequence[Mapping[str, Any]], golden: Sequence[Mapping[str, Any]]
) -> list[str]:
    """Return human-readable problems; an empty list means ``actual`` matches ``golden``."""
    if len(actual) != len(golden):
        return [f"record count {len(actual)} != golden {len(golden)}"]
    problems: list[str] = []
    for i, (a, g) in enumerate(zip(actual, golden, strict=True)):
        for field in EXACT_FIELDS:
            if a.get(field) != g.get(field):
                problems.append(f"[{i}] {field}: {a.get(field)!r} != golden {g.get(field)!r}")
        if _error_code(a.get("error")) != _error_code(g.get("error")):
            problems.append(f"[{i}] error: {a.get('error')!r} != golden {g.get('error')!r}")
        sim = text_similarity(a.get("text", ""), g.get("text", ""))
        if sim < TEXT_SIMILARITY_THRESHOLD:
            problems.append(f"[{i}] text similarity {sim:.3f} < {TEXT_SIMILARITY_THRESHOLD}")
    return problems


def _money(value: str) -> str:
    return f"${Decimal(value):,.2f}"


def missing_key_fields(text: str, truth: Mapping[str, Any]) -> list[str]:
    """Ground-truth key values (names, address, parcel, principal) absent from ``text``."""
    haystack = normalize_text(text)
    missing: list[str] = []
    for inst in truth.get("instruments", []):
        wanted = [inst.get(k) for k in ("grantor", "grantee", "address_raw", "parcel_key")]
        if inst.get("principal"):
            wanted.append(_money(inst["principal"]))
        missing += [w for w in wanted if w and normalize_text(w) not in haystack]
    return missing


GOLDEN_TIMESTAMP = "1970-01-01T00:00:00Z"


def canonicalize(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Golden form of extractor output: identical but with a pinned ``extracted_at``."""
    return [{**r, "extracted_at": GOLDEN_TIMESTAMP} for r in records]


EXPECTED_REJECTED = frozenset({"corrupt", "oversize"})


def unexpected_failures(by_fixture: Mapping[str, Sequence[Mapping[str, Any]]]) -> list[str]:
    """Failed records on fixtures that are supposed to extract (guards golden regeneration)."""
    return [
        f"{name}: {rec.get('error')}"
        for name, records in by_fixture.items()
        if name not in EXPECTED_REJECTED
        for rec in records
        if rec.get("status") == "failed"
    ]
