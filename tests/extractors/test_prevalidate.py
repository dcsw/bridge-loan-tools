"""WU5a DoD 4: pre-validation rejects corrupt / oversize / non-PDF input."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.ingest.prevalidate import Limits, PrevalidationError, prevalidate

PDFS = Path(__file__).resolve().parents[1] / "fixtures" / "pdfs"


def _code(data: bytes, limits: Limits | None = None) -> str:
    with pytest.raises(PrevalidationError) as exc:
        prevalidate(data, limits or Limits())
    return exc.value.code


@pytest.mark.parametrize(
    "name", ["mortgage", "deed", "discharge", "multi_instrument", "scanned_image"]
)
def test_valid_fixtures_pass(name):
    prevalidate((PDFS / f"{name}.pdf").read_bytes(), Limits())


def test_corrupt_fixture_rejected_as_truncated():
    assert _code((PDFS / "corrupt.pdf").read_bytes()) == "truncated"


def test_oversize_fixture_rejected_by_default_cap():
    assert _code((PDFS / "oversize.pdf").read_bytes()) == "too_large"


def test_empty_rejected():
    assert _code(b"") == "empty"


@pytest.mark.parametrize(
    "data",
    [b"PK\x03\x04 zip", b"<html>%PDF-1.4 not at start</html>", b"\x89PNG\r\n\x1a\n" + b"x" * 50],
)
def test_non_pdf_rejected_by_magic_bytes(data):
    assert _code(data) == "not_pdf"


def test_magic_may_follow_small_preamble_within_first_1024_bytes():
    data = b"\n" * 10 + (PDFS / "deed.pdf").read_bytes()
    prevalidate(data, Limits())


def test_missing_eof_marker_rejected():
    assert _code(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n") == "truncated"


def test_page_cap_enforced_on_multi_page_fixture():
    data = (PDFS / "multi_instrument.pdf").read_bytes()
    assert _code(data, Limits(max_pages=2)) == "too_many_pages"
    prevalidate(data, Limits(max_pages=3))


def test_size_cap_is_configurable():
    data = (PDFS / "deed.pdf").read_bytes()
    assert _code(data, Limits(max_bytes=100)) == "too_large"


def test_limits_from_env():
    limits = Limits.from_env({"EXTRACT_MAX_BYTES": "123", "EXTRACT_MAX_PAGES": "7"})
    assert (limits.max_bytes, limits.max_pages) == (123, 7)
    assert Limits.from_env({}) == Limits()
