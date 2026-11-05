"""Docling extractor container entrypoint (layout + TableFormer + RapidOCR, all offline).

All models are baked into the image under ``DOCLING_ARTIFACTS_PATH`` and
``enable_remote_services`` is off, so nothing is downloaded at runtime. OCR is enabled, so
image-only pages are OCRed (rule: Docling OCRs bitmap content; if OCR still yields no text
the shared runner flags the page ``needs_ocr``).
"""

from __future__ import annotations

import hashlib
import os
from importlib.metadata import version as _dist_version
from io import BytesIO
from pathlib import Path
from typing import Any

from backend.ingest.extractors.runner import ExtractionResult, PageResult, main

NAME = "docling"
ARTIFACTS_PATH = os.environ.get("DOCLING_ARTIFACTS_PATH", "/models")


def _models_digest() -> str:
    """Short digest of the baked-model manifest, so bronze/goldens are tied to model bytes."""
    manifest = Path(os.environ.get("MODELS_MANIFEST", "/app/models.sha256"))
    return hashlib.sha256(manifest.read_bytes()).hexdigest()[:12]


def extractor_version() -> str:
    return f"docling=={_dist_version('docling')}+models.{_models_digest()}"


def _build_converter() -> Any:
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    options = PdfPipelineOptions(
        artifacts_path=ARTIFACTS_PATH,
        do_ocr=True,
        do_table_structure=True,
        enable_remote_services=False,
    )
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    )


_converter: Any = None


def _get_converter() -> Any:
    global _converter
    if _converter is None:
        _converter = _build_converter()
    return _converter


def _table_rows(table: Any) -> list[list[str]]:
    rows: list[list[str]] = []
    for grid_row in table.data.grid:
        rows.append([cell.text for cell in grid_row])
    return rows


def extract(data: bytes, max_pages: int) -> ExtractionResult:
    from docling.datamodel.base_models import DocumentStream

    result = _get_converter().convert(
        DocumentStream(name="input.pdf", stream=BytesIO(data)),
        page_range=(1, max_pages),
        raises_on_error=True,
    )
    doc = result.document
    tables_by_page: dict[int, list[list[list[str]]]] = {}
    for table in doc.tables:
        page_no = table.prov[0].page_no if table.prov else 1
        tables_by_page.setdefault(page_no, []).append(_table_rows(table))
    pages = [
        PageResult(
            page_no,
            doc.export_to_markdown(page_no=page_no),
            tables_by_page.get(page_no, []),
        )
        for page_no in sorted(doc.pages)
    ]
    return ExtractionResult(pages=pages, truncated=result.input.page_count > max_pages)


if __name__ == "__main__":
    _get_converter()  # load models once in the parent; per-document work forks from here
    main(NAME, extractor_version(), extract)
