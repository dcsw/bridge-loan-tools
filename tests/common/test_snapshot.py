"""Snapshot generator: validates against models, deterministic, PII split."""

import hashlib
import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from backend.common.models import LoanFeature
from backend.common.snapshot import (
    DETAIL_FILE,
    LIST_FILE,
    SnapshotRow,
    build_snapshot,
    synthetic_rows,
    tree_hash,
    validate_snapshot,
)

FIXED = "2026-01-01T00:00:00+00:00"


def test_synthetic_rows_deterministic_and_valid():
    a = synthetic_rows(50, seed=7)
    b = synthetic_rows(50, seed=7)
    assert a == b
    assert synthetic_rows(50, seed=8) != a
    assert len({r.loan.loan_id for r in a}) == 50
    assert all(isinstance(r, SnapshotRow) for r in a)
    assert any(r.loan.geocode_status == "no_match" for r in synthetic_rows(200, seed=7))


def test_build_and_validate(tmp_path: Path):
    out = build_snapshot(synthetic_rows(30, seed=1), tmp_path, generated_at=FIXED)
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["row_count"] == 30
    assert manifest["classifier_version"] == "placeholder-all-true"
    assert manifest["files"]["list"]["path"] == LIST_FILE
    assert manifest["files"]["detail"]["path"] == DETAIL_FILE
    assert validate_snapshot(out) == 30


def test_list_has_no_pii_detail_has_borrower(tmp_path: Path):
    out = build_snapshot(synthetic_rows(20, seed=1), tmp_path, generated_at=FIXED)
    lst = pq.read_table(out / LIST_FILE)
    det = pq.read_table(out / DETAIL_FILE)
    assert "borrower" not in lst.column_names
    assert set(LoanFeature.model_fields) <= set(lst.column_names)
    assert "borrower" in det.column_names
    borrowers = {b for b in det.column("borrower").to_pylist()}
    blob = (out / LIST_FILE).read_bytes()
    assert not any(b.encode() in blob for b in borrowers)


def test_single_fixture_variant(tmp_path: Path):
    out = build_snapshot(synthetic_rows(1, seed=1), tmp_path, generated_at=FIXED)
    assert validate_snapshot(out) == 1


def test_5k_variant_deterministic(tmp_path: Path):
    h1 = tree_hash(
        build_snapshot(synthetic_rows(5000, seed=42), tmp_path / "a", generated_at=FIXED)
    )
    h2 = tree_hash(
        build_snapshot(synthetic_rows(5000, seed=42), tmp_path / "b", generated_at=FIXED)
    )
    assert h1 == h2
    h3 = tree_hash(
        build_snapshot(synthetic_rows(5000, seed=43), tmp_path / "c", generated_at=FIXED)
    )
    assert h1 != h3


def test_validate_rejects_tampering(tmp_path: Path):
    out = build_snapshot(synthetic_rows(5, seed=1), tmp_path, generated_at=FIXED)
    (out / LIST_FILE).write_bytes(b"garbage")
    with pytest.raises(ValueError, match="sha256"):
        validate_snapshot(out)


def test_validate_rejects_invalid_rows(tmp_path: Path):
    import pyarrow as pa

    out = build_snapshot(synthetic_rows(5, seed=1), tmp_path, generated_at=FIXED)
    table = pq.read_table(out / LIST_FILE)
    idx = table.column_names.index("bridge_score")
    bad = table.set_column(idx, "bridge_score", pa.array([500] * table.num_rows, pa.int64()))
    pq.write_table(bad, out / LIST_FILE)
    m = json.loads((out / "manifest.json").read_text())
    m["files"]["list"]["sha256"] = hashlib.sha256((out / LIST_FILE).read_bytes()).hexdigest()
    (out / "manifest.json").write_text(json.dumps(m))
    with pytest.raises(ValueError):
        validate_snapshot(out)


def test_validate_rejects_row_count_mismatch(tmp_path: Path):
    out = build_snapshot(synthetic_rows(5, seed=1), tmp_path, generated_at=FIXED)
    m = json.loads((out / "manifest.json").read_text())
    m["row_count"] = 6
    (out / "manifest.json").write_text(json.dumps(m))
    with pytest.raises(ValueError, match="row_count"):
        validate_snapshot(out)


def test_empty_rows_rejected(tmp_path: Path):
    with pytest.raises(ValueError):
        build_snapshot([], tmp_path, generated_at=FIXED)
