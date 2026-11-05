"""Unstructured extractor container entrypoint (hi_res layout model + Tesseract OCR, offline).

Layout/table models are baked into the image (``HF_HOME``), NLTK data into ``NLTK_DATA``;
runtime has no network. hi_res runs Tesseract on image-only pages, so a scanned page is
OCRed; if that still yields no text the shared runner flags the page ``needs_ocr``.
"""

from __future__ import annotations

import hashlib
import os
from collections import defaultdict
from importlib.metadata import version as _dist_version
from io import BytesIO
from pathlib import Path

from backend.ingest.extractors.runner import ExtractionResult, PageResult, main

NAME = "unstructured"


def _models_digest() -> str:
    """Short digest of the baked-model manifest, so bronze/goldens are tied to model bytes."""
    manifest = Path(os.environ.get("MODELS_MANIFEST", "/app/models.sha256"))
    return hashlib.sha256(manifest.read_bytes()).hexdigest()[:12]


def extractor_version() -> str:
    return f"unstructured=={_dist_version('unstructured')}+models.{_models_digest()}"


def limit_pages(data: bytes, max_pages: int) -> tuple[bytes, bool]:
    """Count pages with a real parser (pypdf; sees pages inside object streams) and slice.

    hi_res layout inference runs on every page it is given, so the cap must be applied
    *before* partition_pdf. Returns (pdf bytes with at most max_pages pages, was_truncated).
    """
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(BytesIO(data))
    if len(reader.pages) <= max_pages:
        return data, False
    writer = PdfWriter()
    for page in reader.pages[:max_pages]:
        writer.add_page(page)
    out = BytesIO()
    writer.write(out)
    return out.getvalue(), True


def extract(data: bytes, max_pages: int) -> ExtractionResult:
    from unstructured.partition.pdf import partition_pdf

    data, truncated = limit_pages(data, max_pages)
    elements = partition_pdf(
        file=BytesIO(data),
        strategy="hi_res",
        infer_table_structure=True,
        languages=["eng"],
    )
    texts: dict[int, list[str]] = defaultdict(list)
    tables: dict[int, list[dict[str, str]]] = defaultdict(list)
    for el in elements:
        page_no = getattr(el.metadata, "page_number", None) or 1
        if page_no > max_pages:
            continue
        if el.text:
            texts[page_no].append(el.text)
        if el.category == "Table":
            tables[page_no].append(
                {"text": el.text, "html": getattr(el.metadata, "text_as_html", None) or ""}
            )
    page_numbers = sorted(set(texts) | set(tables)) or [1]
    return ExtractionResult(
        pages=[PageResult(n, "\n".join(texts[n]), tables[n]) for n in page_numbers],
        truncated=truncated,
    )


if __name__ == "__main__":
    main(NAME, extractor_version(), extract)
