"""PyMuPDF extractor container entrypoint (text layer + ruled tables; no OCR).

PyMuPDF is AGPL-3.0; see docs/adr/0004-pymupdf-agpl.md. Image-only pages yield no text and
are flagged ``needs_ocr`` by the shared runner (this extractor never OCRs).
"""

from __future__ import annotations

from importlib.metadata import version as _dist_version

from backend.ingest.extractors.runner import ExtractionResult, PageResult, main

NAME = "pymupdf"


def extractor_version() -> str:
    return f"pymupdf=={_dist_version('pymupdf')}"


def extract(data: bytes, max_pages: int) -> ExtractionResult:
    import pymupdf  # imported lazily so the module is importable without the dependency

    pages: list[PageResult] = []
    total = 0
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        total = doc.page_count
        for index in range(min(doc.page_count, max_pages)):
            page = doc[index]
            tables = [t.extract() for t in page.find_tables().tables]
            pages.append(PageResult(index + 1, page.get_text("text"), tables))
    return ExtractionResult(pages=pages, truncated=total > max_pages)


if __name__ == "__main__":
    main(NAME, extractor_version(), extract)
