"""Committed fixtures: well formed, regenerable, and free of real PII."""

import json
import re
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from backend.common.snapshot import DETAIL_FILE, validate_snapshot
from tests.fixtures.generate import FIXTURE_SEED, OVERSIZE_BYTES, generate_all

FIX = Path(__file__).resolve().parents[1] / "fixtures"
PDFS = ["mortgage", "deed", "discharge", "scanned_image", "multi_instrument", "corrupt", "oversize"]

# Real-world names/organizations that must never appear in fixtures.
DENYLIST = {
    "john smith",
    "jane doe",
    "barack obama",
    "donald trump",
    "joe biden",
    "bank of america",
    "wells fargo",
    "jpmorgan",
    "quicken loans",
    "rocket mortgage",
}
SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
EMAIL = re.compile(r"[\w.+-]+@([\w-]+\.[\w.-]+)")
PHONE = re.compile(r"\(?\b\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b")


def _text_files():
    for p in FIX.rglob("*"):
        if p.suffix in {".json", ".md", ".txt"} and p.is_file():
            yield p


def test_expected_files_exist():
    for stem in PDFS:
        assert (FIX / "pdfs" / f"{stem}.pdf").is_file(), stem
    for stem in ("mortgage", "deed", "discharge", "scanned_image", "multi_instrument"):
        assert (FIX / "truth" / f"{stem}.json").is_file()
    for status in ("matched", "low_match", "no_match"):
        assert (FIX / "census" / f"{status}.json").is_file()
    assert (FIX / "snapshots" / "single" / "manifest.json").is_file()
    assert (FIX / "topojson" / "mattapoisett.topo.json").is_file()
    assert (FIX / "topojson" / "README.md").is_file()


def test_pdf_shapes():
    assert (FIX / "pdfs" / "mortgage.pdf").read_bytes().startswith(b"%PDF")
    assert not (FIX / "pdfs" / "corrupt.pdf").read_bytes().rstrip().endswith(b"%%EOF")
    assert (FIX / "pdfs" / "oversize.pdf").stat().st_size >= OVERSIZE_BYTES
    assert b"/Font" not in (FIX / "pdfs" / "scanned_image.pdf").read_bytes()  # image only


def test_truth_matches_multi_instrument():
    truth = json.loads((FIX / "truth" / "multi_instrument.json").read_text())
    assert [i["ordinal"] for i in truth["instruments"]] == [0, 1, 2]
    assert [i["doc_type"] for i in truth["instruments"]] == ["deed", "mortgage", "discharge"]
    for entry in truth["instruments"]:
        assert {"grantor", "grantee", "address_raw", "recorded_date"} <= set(entry)


def test_census_recordings():
    matched = json.loads((FIX / "census" / "matched.json").read_text())
    low = json.loads((FIX / "census" / "low_match.json").read_text())
    none = json.loads((FIX / "census" / "no_match.json").read_text())
    assert matched["expected_geocode_status"] == "matched"
    assert matched["response"]["result"]["addressMatches"]
    assert low["expected_geocode_status"] == "low_match"
    assert none["response"]["result"]["addressMatches"] == []


def test_regenerate_is_identical(tmp_path: Path):
    """Committed fixtures equal a fresh generation with the fixed seed."""
    generate_all(tmp_path, with_5k=False)
    for rel in [
        *(f"pdfs/{s}.pdf" for s in PDFS),
        "truth/multi_instrument.json",
        "census/matched.json",
        "snapshots/single/manifest.json",
    ]:
        assert (tmp_path / rel).read_bytes() == (FIX / rel).read_bytes(), rel
    assert FIXTURE_SEED == 20261105


def test_snapshots_validate():
    assert validate_snapshot(FIX / "snapshots" / "single") == 1


def test_5k_snapshot_generated_deterministically(tmp_path: Path):
    from backend.common.snapshot import tree_hash

    generate_all(tmp_path, with_5k=True)
    first = tree_hash(tmp_path / "snapshots" / "synthetic_5k")
    assert validate_snapshot(tmp_path / "snapshots" / "synthetic_5k") == 5000
    generate_all(tmp_path / "again", with_5k=True)
    assert tree_hash(tmp_path / "again" / "snapshots" / "synthetic_5k") == first


def test_topojson_structure():
    topo = json.loads((FIX / "topojson" / "mattapoisett.topo.json").read_text())
    assert topo["type"] == "Topology"
    geom = topo["objects"]["mattapoisett"]["geometries"][0]
    assert geom["type"] == "Polygon"
    ring = topo["arcs"][geom["arcs"][0][0]]
    assert len(ring) >= 4
    readme = (FIX / "topojson" / "README.md").read_text().lower()
    assert "not official" in readme and "placeholder" in readme


@pytest.mark.parametrize("path", sorted(_text_files()), ids=lambda p: str(p.relative_to(FIX)))
def test_no_pii_in_text_fixtures(path: Path):
    text = path.read_text()
    low = text.lower()
    assert not SSN.search(text)
    assert not PHONE.search(text)
    for domain in EMAIL.findall(text):
        assert domain.endswith(("example.com", "example.org", "example.net")), domain
    for bad in DENYLIST:
        assert bad not in low


def test_all_person_names_are_synthetic():
    """Every person name in truth/snapshot detail is reproducible from the fixed Faker seed."""
    from tests.fixtures.generate import synthetic_person_names

    allowed = synthetic_person_names()
    names = set()
    for p in (FIX / "truth").glob("*.json"):
        for i in json.loads(p.read_text())["instruments"]:
            names.update(n for n in (i.get("grantor"), i.get("grantee")) if n)
    det = pq.read_table(FIX / "snapshots" / "single" / DETAIL_FILE)
    names.update(det.column("borrower").to_pylist())
    person = {n for n in names if not re.search(r"LLC|BANK|CREDIT UNION|INC", n.upper())}
    assert person
    assert person <= allowed


def test_pdfs_contain_no_real_pii_markers():
    for stem in PDFS:
        data = (FIX / "pdfs" / f"{stem}.pdf").read_bytes()
        assert not SSN.search(data.decode("latin-1")) or stem == "oversize"
