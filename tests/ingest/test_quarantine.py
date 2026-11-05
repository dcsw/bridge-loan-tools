"""Pure-Python (no Spark) tests for the extract-out loader and quarantine routing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.ingest import quarantine
from backend.ingest.quarantine import load_extractor_dir

GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "golden"
SHA = "a" * 64


def _rec(**over):
    base = {
        "doc_id": SHA,
        "extractor": "pymupdf",
        "extractor_version": "pymupdf==1.27.2",
        "page_no": 1,
        "text": "hello",
        "tables_json": "[]",
        "extracted_at": "1970-01-01T00:00:00Z",
        "status": "ok",
        "error": None,
    }
    return {**base, **over}


@pytest.mark.parametrize("extractor", ["docling", "pymupdf", "unstructured"])
def test_golden_dir_splits_into_records_and_quarantine(extractor):
    result = load_extractor_dir(extractor, GOLDEN / extractor)
    assert result.files_read == 7
    assert len(result.records) == 7  # deed, discharge, mortgage, scanned_image, 3 pages
    assert {r.status for r in result.records} <= {"ok", "partial"}
    reasons = {(r.source_file, r.reason_code) for r in result.rejects}
    assert reasons == {("corrupt.json", "status_failed"), ("oversize.json", "status_failed")}
    assert all(r.extractor == extractor and r.reason for r in result.rejects)
    assert any("too_large" in r.reason for r in result.rejects)


def test_scanned_partial_is_kept_in_bronze():
    result = load_extractor_dir("pymupdf", GOLDEN / "pymupdf")
    assert any(r.status == "partial" for r in result.records)


def test_malformed_json_is_quarantined_not_fatal(tmp_path):
    (tmp_path / "bad.json").write_text("{not json")
    (tmp_path / f"{SHA}.json").write_text(json.dumps([_rec()]))
    result = load_extractor_dir("pymupdf", tmp_path)
    assert len(result.records) == 1
    [rej] = result.rejects
    assert (rej.source_file, rej.reason_code, rej.record_index) == (
        "bad.json",
        "malformed_json",
        -1,
    )
    assert rej.doc_id is None


def test_non_list_top_level_is_quarantined(tmp_path):
    (tmp_path / "obj.json").write_text(json.dumps(_rec()))
    [rej] = load_extractor_dir("pymupdf", tmp_path).rejects
    assert rej.reason_code == "not_a_list"


def test_unreadable_file_is_quarantined(tmp_path):
    (tmp_path / "bin.json").write_bytes(b"\xff\xfe\x00bad")
    [rej] = load_extractor_dir("pymupdf", tmp_path).rejects
    assert rej.reason_code == "unreadable"


def test_validation_failures_are_quarantined_per_record(tmp_path):
    payload = [_rec(page_no=1), _rec(page_no=0), _rec(page_no=2, doc_id="zz"), "not-a-dict"]
    payload.append(_rec(page_no=3, status="failed", error=None))
    (tmp_path / "mix.json").write_text(json.dumps(payload))
    result = load_extractor_dir("pymupdf", tmp_path)
    assert [r.page_no for r in result.records] == [1]
    assert [(r.record_index, r.reason_code) for r in result.rejects] == [
        (1, "validation_error"),
        (2, "validation_error"),
        (3, "validation_error"),
        (4, "validation_error"),
    ]
    assert result.rejects[1].doc_id == "zz"  # raw doc_id retained for triage
    assert result.rejects[2].doc_id is None  # non-dict record has none


def test_extractor_mismatch_is_quarantined(tmp_path):
    (tmp_path / "x.json").write_text(json.dumps([_rec(extractor="docling")]))
    result = load_extractor_dir("pymupdf", tmp_path)
    assert result.records == []
    assert result.rejects[0].reason_code == "extractor_mismatch"


def test_duplicate_key_second_is_quarantined(tmp_path):
    (tmp_path / "a.json").write_text(json.dumps([_rec(text="first")]))
    (tmp_path / "b.json").write_text(json.dumps([_rec(text="second")]))
    result = load_extractor_dir("pymupdf", tmp_path)
    assert [r.text for r in result.records] == ["first"]
    assert [(r.source_file, r.reason_code) for r in result.rejects] == [("b.json", "duplicate_key")]


def test_payload_is_truncated(tmp_path):
    (tmp_path / "big.json").write_text("{" + "x" * 20000)
    [rej] = load_extractor_dir("pymupdf", tmp_path).rejects
    assert len(rej.payload) <= quarantine.MAX_PAYLOAD_CHARS


def test_only_top_level_json_files_are_read(tmp_path):
    (tmp_path / "notes.txt").write_text("ignore me")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "x.json").write_text("{")
    result = load_extractor_dir("pymupdf", tmp_path)
    assert (result.files_read, result.records, result.rejects) == (0, [], [])


def test_missing_dir_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_extractor_dir("pymupdf", tmp_path / "nope")


def test_file_instead_of_dir_raises_not_a_directory(tmp_path):
    f = tmp_path / "pymupdf"
    f.write_text("x")
    with pytest.raises(NotADirectoryError):
        load_extractor_dir("pymupdf", f)
