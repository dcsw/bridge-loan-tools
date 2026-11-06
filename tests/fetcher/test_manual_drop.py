"""Manual PDF drop: same drop-dir contract as the fetcher, and it feeds raw-ingest unchanged."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import pytest

from backend.common.models import Manifest
from backend.fetcher.manual_drop import ManualDropError, drop_gap, drop_pdf, main
from backend.fetcher.raw_ingest import RawIngestor

PDF = b"%PDF-1.4\nsynthetic manual drop\n%%EOF"
URL = "https://example.test/instrument?book=101&page=11"
NOW = datetime(2026, 11, 6, tzinfo=UTC)


@pytest.fixture
def pdf(tmp_path):
    p = tmp_path / "in.pdf"
    p.write_bytes(PDF)
    return p


def test_drop_pdf_writes_sha_named_pair_with_valid_manifest(pdf, tmp_path):
    drop = tmp_path / "drop"
    doc_id = drop_pdf(pdf, url=URL, town="mattapoisett", drop_dir=drop, now=NOW)
    assert doc_id == hashlib.sha256(PDF).hexdigest()
    assert (drop / f"{doc_id}.pdf").read_bytes() == PDF
    row = Manifest.model_validate_json((drop / f"{doc_id}.json").read_bytes())
    assert (row.status, row.town, row.source_url, row.size_bytes) == (
        "fetched",
        "mattapoisett",
        URL,
        len(PDF),
    )
    assert row.fetched_at == NOW and row.gap_reason is None


@pytest.mark.parametrize(
    ("data", "match"),
    [(b"", "size"), (b"<html>captcha</html>", "%PDF")],
)
def test_rejects_empty_and_non_pdf(tmp_path, data, match):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(data)
    with pytest.raises(ManualDropError, match=match):
        drop_pdf(bad, url=URL, town="rye", drop_dir=tmp_path / "drop")
    assert not (tmp_path / "drop").exists()


@pytest.mark.parametrize(
    ("url", "town"),
    [("", "rye"), ("a\nb", "rye"), (URL, "Rye"), (URL, "../x"), (URL, "r")],
)
def test_rejects_bad_url_or_town(pdf, tmp_path, url, town):
    with pytest.raises(ManualDropError):
        drop_pdf(pdf, url=url, town=town, drop_dir=tmp_path / "drop")


def test_missing_file(tmp_path):
    with pytest.raises(ManualDropError, match="not a file"):
        drop_pdf(tmp_path / "nope.pdf", url=URL, town="rye", drop_dir=tmp_path)


def test_gap_row_has_reason_and_no_pdf(tmp_path):
    doc_id = drop_gap("paid", url=URL, town="madison", drop_dir=tmp_path, now=NOW)
    row = json.loads((tmp_path / f"{doc_id}.json").read_text())
    assert (row["status"], row["gap_reason"], row["size_bytes"]) == ("gap", "paid", 0)
    assert not list(tmp_path.glob("*.pdf"))


def test_unknown_gap_reason(tmp_path):
    with pytest.raises(ManualDropError):
        drop_gap("sad", url=URL, town="rye", drop_dir=tmp_path)


def test_cli_exit_codes(pdf, tmp_path, capsys):
    drop = tmp_path / "d"
    ok = ["--file", str(pdf), "--url", URL, "--town", "rye", "--drop", str(drop)]
    assert main(ok) == 0 and "dropped" in capsys.readouterr().out
    assert main(["--gap", "blocked", "--url", URL, "--town", "rye", "--drop", str(drop)]) == 0
    assert main(["--file", str(pdf), "--url", "", "--town", "rye", "--drop", str(drop)]) == 2
    assert "manual-drop:" in capsys.readouterr().err


class _FakeS3:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def head_object(self, Bucket, Key):  # noqa: N803
        from botocore.exceptions import ClientError

        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")

    def put_object(self, Bucket, Key, Body):  # noqa: N803
        self.objects[Key] = Body


def test_output_is_accepted_by_raw_ingest_and_is_idempotent(pdf, tmp_path):
    drop = tmp_path / "drop"
    doc_id = drop_pdf(pdf, url=URL, town="mattapoisett", drop_dir=drop)
    drop_gap("paid", url=URL + "&other", town="mattapoisett", drop_dir=drop)
    s3 = _FakeS3()
    summary = RawIngestor(s3).ingest_dir(drop)
    assert (summary.uploaded, summary.gaps, summary.rejected) == (1, 1, 0)
    assert s3.objects[f"pdf/{doc_id}.pdf"] == PDF
    drop_pdf(pdf, url=URL, town="mattapoisett", drop_dir=drop)  # same bytes again
    again = RawIngestor(s3).ingest_dir(drop)
    assert (again.uploaded, again.duplicates) == (0, 1)
