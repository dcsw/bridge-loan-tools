"""Runner isolation tests with an injected fake sink (no Spark, no Docker)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.ingest import bronze

GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "golden"


class FakeSink:
    def __init__(self, fail_on: str | None = None, fail_quarantine: bool = False):
        self.fail_on, self.fail_quarantine = fail_on, fail_quarantine
        self.bronze: dict[str, list] = {}
        self.quarantine: list = []

    def write_bronze(self, extractor, records):
        if extractor == self.fail_on:
            raise RuntimeError("injected spark failure")
        self.bronze[extractor] = list(records)
        return len(self.bronze[extractor])

    def write_quarantine(self, rows):
        if self.fail_quarantine:
            raise RuntimeError("injected quarantine failure")
        self.quarantine += list(rows)
        return len(rows)


def test_all_goldens_flow_and_failed_are_quarantined():
    sink = FakeSink()
    out = bronze.run(GOLDEN, bronze.EXTRACTORS, sink)
    assert [o.state for o in out] == ["written"] * 3
    assert {k: len(v) for k, v in sink.bronze.items()} == {e: 7 for e in bronze.EXTRACTORS}
    assert len(sink.quarantine) == 6
    assert bronze.exit_code(out) == 0


def test_missing_extractor_dir_is_skipped_others_proceed(tmp_path):
    (tmp_path / "pymupdf").symlink_to(GOLDEN / "pymupdf")
    sink = FakeSink()
    out = {o.extractor: o for o in bronze.run(tmp_path, bronze.EXTRACTORS, sink)}
    assert out["pymupdf"].state == "written"
    assert out["docling"].state == out["unstructured"].state == "skipped"
    assert set(sink.bronze) == {"pymupdf"}
    assert bronze.exit_code(list(out.values())) == 0


@pytest.mark.parametrize("victim", bronze.EXTRACTORS)
def test_one_extractor_failing_does_not_block_the_others(victim):
    sink = FakeSink(fail_on=victim)
    out = {o.extractor: o for o in bronze.run(GOLDEN, bronze.EXTRACTORS, sink)}
    assert out[victim].state == "failed"
    assert "injected spark failure" in out[victim].detail
    assert set(sink.bronze) == set(bronze.EXTRACTORS) - {victim}
    assert bronze.exit_code(list(out.values())) == 1


def test_quarantine_write_failure_fails_only_that_extractor(tmp_path):
    clean = tmp_path / "docling"
    clean.mkdir()
    (clean / "empty.json").write_text(json.dumps([]))  # no rejects: never touches the sink
    (tmp_path / "pymupdf").symlink_to(GOLDEN / "pymupdf")  # has rejects
    sink = FakeSink(fail_quarantine=True)
    out = {o.extractor: o for o in bronze.run(tmp_path, ("pymupdf", "docling"), sink)}
    assert out["pymupdf"].state == "failed"
    assert out["docling"].state == "written"


def test_path_that_is_a_file_fails_that_extractor_only(tmp_path):
    (tmp_path / "pymupdf").write_text("i am a file")
    (tmp_path / "docling").symlink_to(GOLDEN / "docling")
    sink = FakeSink()
    out = {o.extractor: o for o in bronze.run(tmp_path, ("pymupdf", "docling"), sink)}
    assert out["pymupdf"].state == "failed"
    assert out["docling"].state == "written"


def test_unknown_extractor_name_is_rejected():
    with pytest.raises(ValueError, match="unknown extractor"):
        bronze.run(GOLDEN, ("nope",), FakeSink())


def test_table_names_one_per_extractor():
    assert bronze.BRONZE_TABLES == {
        "docling": "bronze_docling",
        "pymupdf": "bronze_pymupdf",
        "unstructured": "bronze_unstructured",
    }
