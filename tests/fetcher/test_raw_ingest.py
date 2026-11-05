"""raw_ingest: drop dir -> content-addressed raw store + manifest rows (in-memory S3 fake)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from botocore.exceptions import ClientError

from backend.common.models import Manifest
from backend.fetcher.raw_ingest import MANIFEST_PREFIX, PDF_PREFIX, RawIngestor

PDF = b"%PDF-1.4\nsynthetic\n%%EOF"
SHA = hashlib.sha256(PDF).hexdigest()
NOW = datetime(2026, 11, 5, tzinfo=UTC)


class FakeS3:
    """Just enough of the boto3 S3 client; counts puts."""

    def __init__(self):
        self.objects: dict[tuple[str, str], bytes] = {}
        self.puts = 0

    def head_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise ClientError({"Error": {"Code": "404", "Message": "nf"}}, "HeadObject")
        return {}

    def put_object(self, Bucket, Key, Body, **kwargs):
        self.puts += 1
        self.objects[(Bucket, Key)] = Body

    def get_object(self, Bucket, Key):  # pragma: no cover - parity with boto3, unused
        raise NotImplementedError


class BrokenS3(FakeS3):
    def head_object(self, Bucket, Key):
        raise ClientError({"Error": {"Code": "500", "Message": "boom"}}, "HeadObject")


def write_drop(drop, *, url="https://portal.example.test/a.pdf", body=PDF, name=None):
    sha = hashlib.sha256(body).hexdigest()
    row = Manifest(
        doc_id=sha,
        source_url=url,
        fetched_at=NOW,
        town="mattapoisett",
        status="fetched",
        size_bytes=len(body),
    )
    (drop / f"{name or sha}.pdf").write_bytes(body)
    (drop / f"{name or sha}.json").write_text(row.model_dump_json())
    return row


def write_gap(drop, url="https://portal.example.test/blocked.pdf"):
    sha = hashlib.sha256(url.encode()).hexdigest()
    row = Manifest(
        doc_id=sha,
        source_url=url,
        fetched_at=NOW,
        town="mattapoisett",
        status="gap",
        gap_reason="blocked",
        size_bytes=0,
    )
    (drop / f"{sha}.json").write_text(row.model_dump_json())
    return row


def test_uploads_pdf_and_manifest_then_archives_drop_files(tmp_path):
    s3 = FakeS3()
    row = write_drop(tmp_path)
    summary = RawIngestor(s3).ingest_dir(tmp_path)
    assert (summary.uploaded, summary.duplicates, summary.gaps, summary.rejected) == (1, 0, 0, 0)
    assert s3.objects[("raw", f"{PDF_PREFIX}{SHA}.pdf")] == PDF
    stored = Manifest.model_validate(
        json.loads(s3.objects[("raw", f"{MANIFEST_PREFIX}{SHA}.json")])
    )
    assert stored == row
    assert not list(tmp_path.glob("*.pdf")) and not list(tmp_path.glob("*.json"))
    assert (tmp_path / "ingested" / f"{SHA}.pdf").exists()


def test_same_sha256_twice_is_one_object_and_one_manifest_row(tmp_path):
    s3 = FakeS3()
    ingestor = RawIngestor(s3)
    write_drop(tmp_path, url="https://portal.example.test/a.pdf")
    ingestor.ingest_dir(tmp_path)
    # same bytes re-fetched from a different URL
    write_drop(tmp_path, url="https://portal.example.test/copy-of-a.pdf")
    summary = ingestor.ingest_dir(tmp_path)
    assert summary.duplicates == 1 and summary.uploaded == 0
    assert len(s3.objects) == 2  # one pdf + one manifest
    assert s3.puts == 2
    stored = json.loads(s3.objects[("raw", f"{MANIFEST_PREFIX}{SHA}.json")])
    assert stored["source_url"] == "https://portal.example.test/a.pdf"  # first writer wins


def test_gap_rows_get_manifest_but_no_pdf(tmp_path):
    s3 = FakeS3()
    row = write_gap(tmp_path)
    summary = RawIngestor(s3).ingest_dir(tmp_path)
    assert summary.gaps == 1
    assert list(s3.objects) == [("raw", f"{MANIFEST_PREFIX}{row.doc_id}.json")]


def test_gap_row_twice_is_one_manifest_row(tmp_path):
    s3 = FakeS3()
    ingestor = RawIngestor(s3)
    write_gap(tmp_path)
    ingestor.ingest_dir(tmp_path)
    write_gap(tmp_path)
    ingestor.ingest_dir(tmp_path)
    assert s3.puts == 1


def test_hash_mismatch_is_rejected_not_uploaded(tmp_path):
    s3 = FakeS3()
    row = write_drop(tmp_path)
    (tmp_path / f"{row.doc_id}.pdf").write_bytes(b"%PDF-tampered")
    summary = RawIngestor(s3).ingest_dir(tmp_path)
    assert summary.rejected == 1 and s3.objects == {}
    assert (tmp_path / "rejected" / f"{row.doc_id}.json").exists()


def test_missing_pdf_for_fetched_row_is_rejected(tmp_path):
    s3 = FakeS3()
    row = write_drop(tmp_path)
    (tmp_path / f"{row.doc_id}.pdf").unlink()
    assert RawIngestor(s3).ingest_dir(tmp_path).rejected == 1
    assert s3.objects == {}


def test_malformed_row_json_is_rejected_and_others_still_ingested(tmp_path):
    s3 = FakeS3()
    (tmp_path / "junk.json").write_text("{not json")
    (tmp_path / "bad-schema.json").write_text(json.dumps({"doc_id": "nope"}))
    write_drop(tmp_path)
    summary = RawIngestor(s3).ingest_dir(tmp_path)
    assert (summary.uploaded, summary.rejected) == (1, 2)
    assert (tmp_path / "rejected" / "junk.json").exists()


def test_non_pdf_bytes_are_rejected(tmp_path):
    s3 = FakeS3()
    write_drop(tmp_path, body=b"<html>not a pdf</html>")
    summary = RawIngestor(s3).ingest_dir(tmp_path)
    assert summary.rejected == 1 and s3.objects == {}


def test_missing_drop_dir_is_an_empty_run(tmp_path):
    summary = RawIngestor(FakeS3()).ingest_dir(tmp_path / "absent")
    assert (summary.uploaded, summary.rejected) == (0, 0)


def test_s3_errors_other_than_not_found_propagate_and_keep_drop_files(tmp_path):
    import pytest

    row = write_drop(tmp_path)
    with pytest.raises(ClientError):
        RawIngestor(BrokenS3()).ingest_dir(tmp_path)
    assert (tmp_path / f"{row.doc_id}.pdf").exists()
