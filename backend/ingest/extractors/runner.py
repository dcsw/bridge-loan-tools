"""Shared driver for the sandboxed extractor containers.

Reads every file in the (read-only) input dir, pre-validates it, runs the extractor in a
forked child with a hard wall-clock timeout, and writes ``<doc_id>.json`` into the output
dir: a JSON list of bronze records (``backend.common.models.BronzeRecord`` shape).

The failure of one document never affects another; failures are *data* (``status``
"failed" + ``error``), so the process exits 0 unless the sandbox itself is unusable.

OCR rule (documented, uniform): an extractor that cannot produce text for a page
(image-only/scanned) must not guess; the page is emitted ``status="partial"`` with
``error="needs_ocr: ..."`` so later stages can route it to an OCR-capable path.

Stdlib only (copied into every image); extractor libraries live in each ``extract.py``.
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
import os
import signal
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.ingest.prevalidate import Limits, PrevalidationError, prevalidate

DEFAULT_DOC_TIMEOUT_S = 120
MAX_ERROR_LEN = 500


@dataclass
class PageResult:
    page_no: int
    text: str
    tables: list[Any] = field(default_factory=list)


@dataclass
class ExtractionResult:
    pages: list[PageResult]
    needs_ocr: bool = False
    truncated: bool = False  # extractor stopped at max_pages although the document has more


ExtractFn = Callable[[bytes, int], ExtractionResult]


def _child(conn: Any, extract_fn: ExtractFn, data: bytes, max_pages: int) -> None:
    os.setsid()  # own process group so a timeout can kill grandchildren (e.g. tesseract)
    try:
        res = extract_fn(data, max_pages)
        payload = (
            "ok",
            [(p.page_no, p.text, p.tables) for p in res.pages],
            res.needs_ocr,
            res.truncated,
        )
    except BaseException as exc:  # noqa: BLE001 - isolate any parser failure, report as data
        payload = ("err", f"extract_error: {type(exc).__name__}: {exc}"[:MAX_ERROR_LEN])
    try:
        conn.send(payload)
    finally:
        conn.close()
        os._exit(0)


def _extract_isolated(
    extract_fn: ExtractFn, data: bytes, max_pages: int, timeout_s: float
) -> tuple[ExtractionResult | None, str | None]:
    ctx = mp.get_context("fork")
    parent, child_conn = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_child, args=(child_conn, extract_fn, data, max_pages))
    proc.start()
    child_conn.close()
    try:
        if not parent.poll(timeout_s):
            return None, f"timeout: exceeded {timeout_s}s"
        try:
            payload = parent.recv()
        except EOFError:
            proc.join(5)
            return None, f"crashed: extractor process died (exit code {proc.exitcode})"
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)  # child + any grandchildren
        except (ProcessLookupError, PermissionError):
            pass
        proc.join()
        parent.close()
    if payload[0] == "err":
        return None, payload[1]
    pages = [PageResult(n, t, tb) for n, t, tb in payload[1]]
    return ExtractionResult(pages=pages, needs_ocr=payload[2], truncated=payload[3]), None


def _record(
    doc_id: str,
    extractor: str,
    version: str,
    page_no: int,
    text: str,
    tables: list[Any],
    status: str,
    error: str | None,
) -> dict[str, Any]:
    return {
        "doc_id": doc_id,
        "extractor": extractor,
        "extractor_version": version,
        "page_no": page_no,
        "text": text,
        "tables_json": json.dumps(tables, sort_keys=True),
        "extracted_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": status,
        "error": error,
    }


def _failed(doc_id: str, extractor: str, version: str, error: str) -> list[dict[str, Any]]:
    return [_record(doc_id, extractor, version, 1, "", [], "failed", error)]


def _process_doc(
    data: bytes,
    extractor: str,
    version: str,
    extract_fn: ExtractFn,
    limits: Limits,
    timeout_s: float,
) -> list[dict[str, Any]]:
    doc_id = hashlib.sha256(data).hexdigest()
    try:
        prevalidate(data, limits)
    except PrevalidationError as exc:
        return _failed(doc_id, extractor, version, f"prevalidate:{exc}")
    result, error = _extract_isolated(extract_fn, data, limits.max_pages, timeout_s)
    if result is None:
        return _failed(doc_id, extractor, version, error or "unknown")
    pages = sorted(result.pages, key=lambda p: p.page_no)
    truncated = result.truncated or len(pages) > limits.max_pages
    pages = pages[: limits.max_pages]
    if not pages:
        return _failed(doc_id, extractor, version, "no_pages: extractor returned no pages")
    records = []
    for i, page in enumerate(pages):
        status, err = "ok", None
        if result.needs_ocr or not page.text.strip():
            status, err = "partial", "needs_ocr: no extractable text layer on this page"
        elif truncated and i == len(pages) - 1:
            status, err = "partial", f"page_cap: output truncated at {limits.max_pages} pages"
        records.append(
            _record(doc_id, extractor, version, page.page_no, page.text, page.tables, status, err)
        )
    if truncated and records[-1]["error"] is None:
        records[-1]["status"] = "partial"
        records[-1]["error"] = f"page_cap: output truncated at {limits.max_pages} pages"
    return records


def _write_atomic(path: Path, payload: list[dict[str, Any]]) -> None:
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, prefix=".tmp-", suffix=".json", delete=False
    ) as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")
        tmp = Path(fh.name)
    tmp.replace(path)


def run(
    *,
    in_dir: Path,
    out_dir: Path,
    extractor: str,
    extractor_version: str,
    extract_fn: ExtractFn,
    limits: Limits,
    doc_timeout_s: float = DEFAULT_DOC_TIMEOUT_S,
) -> int:
    """Process every file in ``in_dir``; returns the process exit code."""
    if not in_dir.is_dir() or not out_dir.is_dir():
        print(f"input/output dir missing: {in_dir} {out_dir}", file=sys.stderr)
        return 2
    for path in sorted(p for p in in_dir.iterdir() if p.is_file()):
        data = path.read_bytes()
        records = _process_doc(
            data, extractor, extractor_version, extract_fn, limits, doc_timeout_s
        )
        doc_id = records[0]["doc_id"]
        _write_atomic(out_dir / f"{doc_id}.json", records)
        print(f"{extractor}: {path.name} -> {doc_id[:12]} {records[0]['status']}", flush=True)
    return 0


def main(extractor: str, version: str, extract_fn: ExtractFn) -> None:
    """Container entrypoint: paths and limits come from env (see compose fragment)."""
    sys.exit(
        run(
            in_dir=Path(os.environ.get("EXTRACT_IN", "/in")),
            out_dir=Path(os.environ.get("EXTRACT_OUT", "/out")),
            extractor=extractor,
            extractor_version=version,
            extract_fn=extract_fn,
            limits=Limits.from_env(os.environ),
            doc_timeout_s=float(os.environ.get("EXTRACT_DOC_TIMEOUT_S", DEFAULT_DOC_TIMEOUT_S)),
        )
    )
