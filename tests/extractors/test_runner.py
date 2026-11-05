"""Shared extractor runner: output contract, rejection, OCR rule, timeout (WU5a)."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest

from backend.common.models import BronzeRecord
from backend.ingest.extractors.runner import ExtractionResult, PageResult, run
from backend.ingest.prevalidate import Limits

PDFS = Path(__file__).resolve().parents[1] / "fixtures" / "pdfs"


def _setup(tmp_path, *names):
    in_dir, out_dir = tmp_path / "in", tmp_path / "out"
    in_dir.mkdir()
    out_dir.mkdir()
    for n in names:
        (in_dir / f"{n}.pdf").write_bytes((PDFS / f"{n}.pdf").read_bytes())
    return in_dir, out_dir


def _read(out_dir, name):
    data = (PDFS / f"{name}.pdf").read_bytes()
    doc_id = hashlib.sha256(data).hexdigest()
    return doc_id, json.loads((out_dir / f"{doc_id}.json").read_text())


def fake_ok(data: bytes, max_pages: int) -> ExtractionResult:
    return ExtractionResult(pages=[PageResult(1, "hello", [{"rows": [["a"]]}])])


def _run(in_dir, out_dir, fn, **kw):
    return run(
        in_dir=in_dir,
        out_dir=out_dir,
        extractor="fake",
        extractor_version="fake==1",
        extract_fn=fn,
        limits=kw.pop("limits", Limits()),
        doc_timeout_s=kw.pop("doc_timeout_s", 20),
    )


def test_output_matches_bronze_contract(tmp_path):
    in_dir, out_dir = _setup(tmp_path, "mortgage", "multi_instrument")
    assert _run(in_dir, out_dir, fake_ok) == 0
    doc_id, recs = _read(out_dir, "mortgage")
    assert len(recs) == 1
    rec = BronzeRecord.model_validate(recs[0])
    assert rec.doc_id == doc_id and rec.extractor == "fake" and rec.status == "ok"
    assert json.loads(rec.tables_json) == [{"rows": [["a"]]}]


def test_blank_text_pages_flag_needs_ocr(tmp_path):
    in_dir, out_dir = _setup(tmp_path, "scanned_image")

    def blank(data, max_pages):
        return ExtractionResult(pages=[PageResult(1, "  \n", [])])

    _run(in_dir, out_dir, blank)
    _, recs = _read(out_dir, "scanned_image")
    rec = BronzeRecord.model_validate(recs[0])
    assert rec.status == "partial" and rec.error.startswith("needs_ocr")


def test_extractor_declared_needs_ocr_is_respected(tmp_path):
    in_dir, out_dir = _setup(tmp_path, "scanned_image")

    def fn(data, max_pages):
        return ExtractionResult(pages=[PageResult(1, "has text", [])], needs_ocr=True)

    _run(in_dir, out_dir, fn)
    _, recs = _read(out_dir, "scanned_image")
    assert recs[0]["status"] == "partial" and recs[0]["error"].startswith("needs_ocr")


@pytest.mark.parametrize(("name", "code"), [("corrupt", "truncated"), ("oversize", "too_large")])
def test_prevalidation_failures_become_failed_records_without_calling_extractor(
    tmp_path, name, code
):
    in_dir, out_dir = _setup(tmp_path, name)

    def boom(data, max_pages):
        raise AssertionError("extractor must not see rejected input")

    assert _run(in_dir, out_dir, boom) == 0
    _, recs = _read(out_dir, name)
    rec = BronzeRecord.model_validate(recs[0])
    assert rec.status == "failed" and rec.error.startswith(f"prevalidate:{code}")


def test_extractor_exception_is_isolated_per_doc(tmp_path):
    in_dir, out_dir = _setup(tmp_path, "mortgage", "deed")
    mort_bytes = (PDFS / "mortgage.pdf").read_bytes()

    def flaky(data, max_pages):
        if data == mort_bytes:
            raise RuntimeError("parser exploded")
        return fake_ok(data, max_pages)

    _run(in_dir, out_dir, flaky)
    _, bad = _read(out_dir, "mortgage")
    _, good = _read(out_dir, "deed")
    assert bad[0]["status"] == "failed" and "parser exploded" in bad[0]["error"]
    assert good[0]["status"] == "ok"


def test_timeout_kills_extractor(tmp_path):
    in_dir, out_dir = _setup(tmp_path, "deed")

    def slow(data, max_pages):
        time.sleep(30)
        return fake_ok(data, max_pages)

    start = time.monotonic()
    _run(in_dir, out_dir, slow, doc_timeout_s=1)
    assert time.monotonic() - start < 15
    _, recs = _read(out_dir, "deed")
    assert recs[0]["status"] == "failed" and recs[0]["error"].startswith("timeout")


def test_max_pages_passed_to_extractor_and_extra_pages_truncated(tmp_path):
    in_dir, out_dir = _setup(tmp_path, "multi_instrument")
    seen = tmp_path / "seen"

    def fn(data, max_pages):  # runs in a forked child: report via file, not shared state
        seen.write_text(str(max_pages))
        return ExtractionResult(pages=[PageResult(i, f"p{i}", []) for i in range(1, 6)])

    _run(in_dir, out_dir, fn, limits=Limits(max_pages=3))
    assert seen.read_text() == "3"
    _, recs = _read(out_dir, "multi_instrument")
    assert [r["page_no"] for r in recs] == [1, 2, 3]
    assert recs[0]["status"] == "ok"
    assert recs[-1]["status"] == "partial" and "page_cap" in recs[-1]["error"]


def test_non_pdf_files_in_input_are_reported(tmp_path):
    in_dir, out_dir = _setup(tmp_path)
    (in_dir / "x.txt").write_bytes(b"hello")
    assert _run(in_dir, out_dir, fake_ok) == 0
    doc_id = hashlib.sha256(b"hello").hexdigest()
    rec = json.loads((out_dir / f"{doc_id}.json").read_text())[0]
    assert rec["status"] == "failed" and "not_pdf" in rec["error"]


def test_only_writes_into_out_dir(tmp_path):
    in_dir, out_dir = _setup(tmp_path, "deed")
    before = sorted(p.name for p in in_dir.iterdir())
    _run(in_dir, out_dir, fake_ok)
    assert sorted(p.name for p in in_dir.iterdir()) == before


def test_extractor_reported_truncation_is_flagged_partial_page_cap(tmp_path):
    in_dir, out_dir = _setup(tmp_path, "multi_instrument")

    def fn(data, max_pages):
        return ExtractionResult(pages=[PageResult(1, "a"), PageResult(2, "b")], truncated=True)

    _run(in_dir, out_dir, fn)
    _, recs = _read(out_dir, "multi_instrument")
    assert recs[0]["status"] == "ok"
    assert recs[-1]["status"] == "partial" and recs[-1]["error"].startswith("page_cap")


def test_timeout_kills_whole_process_group(tmp_path):
    import os
    import subprocess

    in_dir, out_dir = _setup(tmp_path, "deed")
    pidfile = tmp_path / "grandchild.pid"

    def spawns_grandchild(data, max_pages):
        proc = subprocess.Popen(["sleep", "120"])
        pidfile.write_text(str(proc.pid))
        time.sleep(120)

    _run(in_dir, out_dir, spawns_grandchild, doc_timeout_s=2)
    pid = int(pidfile.read_text())
    time.sleep(0.5)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
