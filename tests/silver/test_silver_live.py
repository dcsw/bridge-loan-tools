"""Docker-backed WU6 checks (RUN_STACK=1): the real Spark silver job against the live stack.

bronze (goldens) -> silver.instruments MERGE (idempotent) -> loans -> geocode cache round trip ->
publish from the Iceberg tables -> WU7 API loads the result; plus snapshot expiry/compaction.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest
from silver_helpers import GOLDEN, NOW, ROOT, compose

from backend.api.config import Settings
from backend.api.queries import get_coverage, get_detail, list_loans
from backend.api.schemas import LoanFilters
from backend.api.snapshot import SnapshotStore
from backend.common.models import BronzeRecord, Manifest
from backend.common.snapshot import validate_snapshot
from backend.ingest import geocode as g
from backend.ingest import publish as pub
from backend.ingest import silver_job as sj
from backend.ingest import views

pytestmark = pytest.mark.stack

EXTRACTORS = ("docling", "pymupdf", "unstructured")
NAMES = ("mortgage", "deed", "discharge", "multi_instrument", "scanned_image", "corrupt")


def _run_job(module, extract_out, manifests, work, *extra):
    return compose(
        "run", "--rm", "ingest", module, *extra, check=False,
        env={
            "EXTRACT_OUT_DIR": str(extract_out),
            "MANIFEST_DIR_HOST": str(manifests),
            "SILVER_WORK_DIR": str(work),
        },
    )  # fmt: skip


def _source(env, monkeypatch):
    monkeypatch.setenv("MINIO_ROOT_USER", env["MINIO_ROOT_USER"])
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", env["MINIO_ROOT_PASSWORD"])
    return pub.IcebergSilverSource(
        "http://127.0.0.1:8181/catalog", "bridgeloan", "http://127.0.0.1:9000"
    )


def _rows(table):
    cols = table.column_names
    return sorted(
        (dict(zip(cols, r, strict=True)) for r in zip(*table.to_pydict().values(), strict=True)),
        key=lambda r: tuple(str(v) for v in r.values()),
    )


def test_silver_pipeline_end_to_end(stack, tmp_path, monkeypatch):
    extract = tmp_path / "extract-out"
    for e in EXTRACTORS:
        (extract / e).mkdir(parents=True)
        for n in NAMES:
            shutil.copy(GOLDEN / e / f"{n}.json", extract / e / f"{n}.json")
    manifests, work = tmp_path / "manifests", tmp_path / "work"
    manifests.mkdir()
    work.mkdir()
    work.chmod(0o777)
    doc_ids = {
        BronzeRecord(**r).doc_id
        for n in NAMES
        for r in json.loads((GOLDEN / "pymupdf" / f"{n}.json").read_text())
    }
    for doc_id in doc_ids:
        row = Manifest(
            doc_id=doc_id, source_url=f"https://portal.example.test/docs/{doc_id}.pdf",
            fetched_at=NOW, town="mattapoisett", status="fetched", size_bytes=10,
        )  # fmt: skip
        (manifests / f"{doc_id}.json").write_text(row.model_dump_json())

    bronze = _run_job("backend.ingest.bronze", extract, manifests, work)
    assert bronze.returncode in (0, 1), bronze.stderr[
        -2000:
    ]  # corrupt doc is quarantined, not fatal

    # DoD 1/2/3: first run -> geocodes pending (container is offline), loans keep null geometry.
    first = _run_job("backend.ingest.silver_job", extract, manifests, work)
    assert first.returncode == 0, first.stdout[-2000:] + first.stderr[-2000:]
    assert "instruments_merged=7" in first.stdout and "loans_recomputed=3" in first.stdout
    src = _source(stack, monkeypatch)
    instruments_1, loans_1 = _rows(src.instruments()), _rows(src.loans())
    assert len(instruments_1) == 7 and len(loans_1) == 3
    assert {r["geocode_status"] for r in loans_1} == {"no_match"}
    assert all(r["lat"] is None and r["is_bridge_loan"] for r in loans_1)
    assert {r["classifier_version"] for r in loans_1} == {"placeholder-all-true"}
    requests = (work / "geocode-requests.txt").read_text().split("\n")
    addresses = sorted(a for a in requests if a)
    assert len(addresses) == 3

    # Idempotent MERGE: same rows after a second run.
    second = _run_job("backend.ingest.silver_job", extract, manifests, work)
    assert second.returncode == 0, second.stdout[-2000:] + second.stderr[-2000:]
    assert _rows(src.instruments()) == instruments_1
    assert _rows(src.loans()) == loans_1

    # DoD 4: the host geocode step fills the cache from RECORDED responses; next run maps loans.
    recorded = json.loads((ROOT / "tests/fixtures/census/matched.json").read_text())["response"]
    rc = g.main(
        [
            "--requests", str(work / "geocode-requests.txt"),
            "--cache", str(work / "geocode-cache.jsonl"),
        ],
        transport=g.RecordedTransport({a: recorded for a in addresses}),
    )  # fmt: skip
    assert rc == 0
    third = _run_job("backend.ingest.silver_job", extract, manifests, work)
    assert third.returncode == 0 and "geocode_pending=0" in third.stdout
    loans_3 = _rows(src.loans())
    # the recorded response is for a different street, so the street check rightly says low_match
    assert {r["geocode_status"] for r in loans_3} == {"low_match"}
    assert all(r["lat"] == 41.6532 and r["lon"] == -70.8161 for r in loans_3)

    # DoD 6/7: publish from the Iceberg tables; output validates and loads in the WU7 API.
    snap = tmp_path / "snap"
    result = pub.publish(
        src, pub.LocalObjectStore(snap), manifests=sj.read_manifest_dir(manifests), now=NOW
    )
    assert result.row_count == 3
    assert validate_snapshot(snap / "versions" / result.snapshot_id) == 3
    store = SnapshotStore(
        Settings(gateway_secret="s" * 32, snapshot_base=str(snap), audit_log_path=str(snap / "a"))
    )
    state = store.current()
    collection = list_loans(state, LoanFilters(), 100, None)
    assert collection.total == 3 and collection.unmapped_count == 0
    detail = get_detail(state, collection.features[0].id)
    assert detail is not None and detail.fields and detail.borrower
    (town,) = get_coverage(state).towns
    assert town.documents_fetched == len(doc_ids)

    # The semantic view over the live silver tables equals silver loans row for row.
    import duckdb

    con = duckdb.connect()
    views.register_silver(con, src.loans(), src.instruments())
    views.create_semantic_views(con)
    view_ids = {r[0] for r in con.execute("select loan_id from semantic_loans_v1").fetchall()}
    assert view_ids == {r["loan_id"] for r in loans_3}

    # Iceberg maintenance: scripts/maintenance/expire_snapshots.py on a silver table.
    env = {
        **os.environ,
        "MINIO_ROOT_USER": stack["MINIO_ROOT_USER"],
        "MINIO_ROOT_PASSWORD": stack["MINIO_ROOT_PASSWORD"],
    }
    done = subprocess.run(
        [sys.executable, "scripts/maintenance/expire_snapshots.py", "--table", "silver.loans",
         "--compact", "--retain-last", "1"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=300,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr[-2000:]
    assert "silver.loans: expired" in done.stdout
    assert _rows(src.loans()) == loans_3  # compaction preserved the rows
