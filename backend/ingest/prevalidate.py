"""Cheap, parser-free pre-validation of untrusted PDFs (design 3.5).

Runs *before* any PDF parser sees the bytes, so it deliberately uses only stdlib byte
checks: size cap, ``%PDF-`` magic bytes, a ``%%EOF`` trailer marker (truncated or corrupt
files are rejected here instead of being silently "repaired" by lenient parsers), and a
page-count cap. The page count here is only a cheap regex heuristic (``/Type /Page``
objects and ``/Count`` values) and CAN be bypassed by a PDF that hides its page objects in
compressed object streams. The real cap is enforced inside each extractor with a real parser:
PyMuPDF iterates ``min(page_count, max_pages)``, Docling converts only
``page_range=(1, max_pages)`` (``max_num_pages`` would reject, not truncate), and
Unstructured counts pages with pypdf and slices the PDF to ``max_pages`` before hi_res layout
inference. Truncated output is flagged ``partial`` / ``page_cap`` by the runner (tested).

Stdlib only: this module is copied verbatim into every extractor image.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

MAGIC = b"%PDF-"
MAGIC_WINDOW = 1024
EOF_WINDOW = 1024
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_MAX_PAGES = 200

_PAGE_OBJ = re.compile(rb"/Type\s*/Page(?![A-Za-z])")
_COUNT = re.compile(rb"/Count\s+(\d+)")


class PrevalidationError(ValueError):
    """Input rejected; ``code`` is a stable machine-readable reason."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class Limits:
    max_bytes: int = DEFAULT_MAX_BYTES
    max_pages: int = DEFAULT_MAX_PAGES

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Limits:
        return cls(
            max_bytes=int(env.get("EXTRACT_MAX_BYTES", DEFAULT_MAX_BYTES)),
            max_pages=int(env.get("EXTRACT_MAX_PAGES", DEFAULT_MAX_PAGES)),
        )


def estimate_pages(data: bytes) -> int:
    objs = len(_PAGE_OBJ.findall(data))
    counts = [int(c) for c in _COUNT.findall(data)]
    return max(objs, max(counts, default=0))


def prevalidate(data: bytes, limits: Limits | None = None) -> None:
    """Raise :class:`PrevalidationError` unless ``data`` looks like a sane, bounded PDF."""
    limits = limits or Limits()
    if not data:
        raise PrevalidationError("empty", "zero-length input")
    if len(data) > limits.max_bytes:
        raise PrevalidationError("too_large", f"{len(data)} bytes > cap {limits.max_bytes}")
    if not data[:MAGIC_WINDOW].lstrip(b"\x00\t\r\n \xef\xbb\xbf").startswith(MAGIC):
        raise PrevalidationError("not_pdf", "missing %PDF- magic bytes at start of file")
    if b"%%EOF" not in data[-EOF_WINDOW:]:
        raise PrevalidationError("truncated", "missing %%EOF trailer (truncated or corrupt)")
    pages = estimate_pages(data)
    if pages > limits.max_pages:
        raise PrevalidationError("too_many_pages", f"~{pages} pages > cap {limits.max_pages}")
