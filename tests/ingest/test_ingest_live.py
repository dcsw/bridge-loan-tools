"""Docker-backed WU5b checks (RUN_STACK=1). Runs the real Spark image against the live stack.

* DoD 1: bronze per extractor, idempotent re-run.
* DoD 2: failed / malformed docs land in quarantine, others unaffected.
* DoD 3: one extractor missing / failing does not block the others.
* DoD 4: the ingest container reaches only minio and lakekeeper.
* DoD 5: Spark -> REST catalog smoke, read back by Spark, pyiceberg and DuckDB 2.0.0.
"""

from __future__ import annotations

import json
import shutil

import duckdb
import pytest
from ingest_helpers import GOLDEN, compose, duck
from pyiceberg.catalog import load_catalog
from pyiceberg.io import load_file_io

pytestmark = pytest.mark.stack

EXTRACTORS = ("docling", "pymupdf", "unstructured")
COLUMNS = (
    "doc_id, extractor, extractor_version, page_no, text, tables_json, "
    "CAST(extracted_at AS VARCHAR), status, error"
)
QUARANTINE_SQL = (
    "SELECT extractor, source_file, record_index, doc_id, reason_code, reason, payload, "
    "CAST(quarantined_at AS VARCHAR) FROM lk.bronze.quarantine "
    "ORDER BY extractor, source_file, record_index"
)


def _run_bronze(extract_out, *extra):
    return compose(
        "run", "--rm", "ingest", "backend.ingest.bronze", *extra,
        check=False, extract_out=extract_out,
    )  # fmt: skip


def _rows(con, table, columns=COLUMNS):
    return con.execute(
        f"SELECT {columns} FROM lk.bronze.{table} ORDER BY doc_id, page_no"
    ).fetchall()


def _copy_goldens(dest):
    for extractor in EXTRACTORS:
        shutil.copytree(GOLDEN / extractor, dest / extractor)
    return dest


def test_bronze_per_extractor_is_idempotent_and_quarantines_failed(stack, tmp_path):
    out = _copy_goldens(tmp_path / "extract-out")
    first = _run_bronze(out)
    assert first.returncode == 0, first.stdout[-2000:] + first.stderr[-4000:]
    con = duck(stack)
    snapshot = {e: _rows(con, f"bronze_{e}") for e in EXTRACTORS}
    quarantine = con.execute(QUARANTINE_SQL).fetchall()

    for extractor, rows in snapshot.items():
        assert len(rows) == 7, extractor  # deed, discharge, mortgage, scanned, 3 multi pages
        assert {r[1] for r in rows} == {extractor}
        assert {r[7] for r in rows} <= {"ok", "partial"}
    assert {(q[0], q[1], q[4]) for q in quarantine} == {
        (e, f, "status_failed") for e in EXTRACTORS for f in ("corrupt.json", "oversize.json")
    }
    assert all(q[5] for q in quarantine)  # reason retained

    second = _run_bronze(out)
    assert second.returncode == 0, second.stderr[-4000:]
    con = duck(stack)
    assert {e: _rows(con, f"bronze_{e}") for e in EXTRACTORS} == snapshot  # no dupes, same values
    assert con.execute(QUARANTINE_SQL).fetchall() == quarantine


def test_changed_record_is_updated_not_duplicated(stack, tmp_path):
    out = tmp_path / "extract-out"
    shutil.copytree(GOLDEN / "pymupdf", out / "pymupdf")
    target = out / "pymupdf" / "mortgage.json"
    records = json.loads(target.read_text())
    records[0]["text"] = "UPDATED TEXT"
    target.write_text(json.dumps(records))
    assert _run_bronze(out, "--extractors", "pymupdf").returncode == 0
    rows = (
        duck(stack)
        .execute(
            "SELECT count(*), count(DISTINCT (doc_id, page_no)), "
            "count(*) FILTER (WHERE text = 'UPDATED TEXT') FROM lk.bronze.bronze_pymupdf"
        )
        .fetchone()
    )
    assert rows == (7, 7, 1)


def test_one_extractor_failing_or_missing_does_not_block_others(stack, tmp_path):
    out = tmp_path / "extract-out"
    out.mkdir()
    (out / "pymupdf").write_text("a file, not a directory: this extractor fails")
    shutil.copytree(GOLDEN / "docling", out / "docling")
    (out / "docling" / "garbage.json").write_text("{definitely not json")
    # unstructured: directory missing entirely
    try:
        before = len(_rows(duck(stack), "bronze_pymupdf"))
    except duckdb.CatalogException:  # table not created yet (test run in isolation)
        before = None

    result = _run_bronze(out)
    assert result.returncode == 1, result.stdout[-2000:] + result.stderr[-3000:]
    assert "extractor=pymupdf state=failed" in result.stdout
    assert "extractor=docling state=written records=7" in result.stdout
    assert "extractor=unstructured state=skipped" in result.stdout

    con = duck(stack)
    assert len(_rows(con, "bronze_docling")) == 7
    if before is None:
        with pytest.raises(duckdb.CatalogException):
            _rows(con, "bronze_pymupdf")
    else:
        assert len(_rows(con, "bronze_pymupdf")) == before  # untouched by the failure
    bad = con.execute(
        "SELECT reason_code FROM lk.bronze.quarantine "
        "WHERE extractor = 'docling' AND source_file = 'garbage.json'"
    ).fetchall()
    assert bad == [("malformed_json",)]


PROBE = r"""
import json, socket, sys
def can_connect(host, port):
    try:
        socket.create_connection((host, port), timeout=3).close()
        return True
    except OSError:
        return False
srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
control = can_connect("127.0.0.1", srv.getsockname()[1])
out = {}
for t in json.loads(sys.argv[1]):
    host, _, port = t.rpartition(":")
    out[t] = can_connect(host, int(port))
print(json.dumps({"control": control, "results": out}))
"""


def test_ingest_network_is_internal(stack):
    out = compose("config", "--format", "json")
    assert json.loads(out.stdout)["networks"]["ingest"]["internal"] is True


def test_ingest_container_reaches_only_minio_and_lakekeeper(stack):
    reachable = ["minio:9000", "lakekeeper:8181"]
    blocked = [
        "169.254.169.254:80",  # cloud metadata IP
        "1.1.1.1:443",  # internet by IP
        "example.com:443",  # internet by name (no DNS)
        "postgres:5432",  # core-only service
        "bootstrap:80",
    ]
    run = compose(
        "run", "--rm", "--no-deps", "--entrypoint", "python", "ingest",
        "-c", PROBE, json.dumps(reachable + blocked), check=False,
    )  # fmt: skip
    assert run.returncode == 0, run.stderr[-3000:]
    result = json.loads(run.stdout.strip().splitlines()[-1])
    assert result["control"] is True, "probe cannot even connect to its own loopback"
    assert result["results"] == {**{t: True for t in reachable}, **{t: False for t in blocked}}


def test_spark_iceberg_smoke_readable_by_pyiceberg_and_duckdb(stack):
    run = compose("run", "--rm", "ingest", "backend.ingest.smoke", "pairing", check=False)
    assert run.returncode == 0, run.stdout[-2000:] + run.stderr[-4000:]
    info = json.loads(run.stdout.strip().splitlines()[-1])
    assert info["table"] == "smoke.spark_smoke_pairing"
    assert info["spark"].startswith("4.0.") and info["java"].startswith("17.")
    expected = [{"id": 1, "label": "alpha"}, {"id": 2, "label": "beta"}, {"id": 3, "label": None}]
    assert info["rows"] == expected  # Spark read-back

    catalog = load_catalog(
        "lakekeeper",
        **{
            "type": "rest",
            "uri": "http://127.0.0.1:8181/catalog",
            "warehouse": "bridgeloan",
            "s3.endpoint": "http://127.0.0.1:9000",
            "s3.path-style-access": "true",
            "s3.access-key-id": stack["MINIO_ROOT_USER"],
            "s3.secret-access-key": stack["MINIO_ROOT_PASSWORD"],
            "s3.region": "us-east-1",
        },
    )
    table = catalog.load_table(info["table"])
    props = {**table.io.properties, "s3.endpoint": "http://127.0.0.1:9000"}
    table.io = load_file_io(props, table.metadata_location)  # ADR-0002 endpoint override
    py_rows = sorted(table.scan().to_arrow().to_pylist(), key=lambda r: r["id"])
    assert [{"id": r["id"], "label": r["label"]} for r in py_rows] == expected
    assert len(table.snapshots()) == 2

    duck_rows = (
        duck(stack)
        .execute("SELECT id, label FROM lk.smoke.spark_smoke_pairing ORDER BY id")
        .fetchall()
    )
    assert duck_rows == [(1, "alpha"), (2, "beta"), (3, None)]
